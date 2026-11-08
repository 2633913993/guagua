"""Run with TEST_MYSQL_URL pointing to a disposable, empty MySQL schema.
Only schemas named guagua_test_* are accepted; production schemas are untouched.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.main import create_app
from app.identity import Identity
from app.quota import Quotas, quota_day
from app.settings import Settings

GOOD = {'image_urls': ['https://cdn.example/1.jpg', 'https://cdn.example/2.jpg']}
WHO = Identity('wxTestApp', 'openidA')
HEADERS = {'X-WX-APPID': WHO.appid, 'X-WX-OPENID': WHO.openid}
URL = 'https://v.douyin.com/test/'


@pytest.fixture(scope='module')
def db():
    url = os.getenv('TEST_MYSQL_URL')
    if not url:
        pytest.skip('TEST_MYSQL_URL is required for real MySQL integration tests')
    parsed = make_url(url)
    assert parsed.drivername == 'mysql+pymysql'
    assert parsed.database.startswith('guagua_test_'), 'Only a disposable test schema is allowed'
    engine = create_engine(url, isolation_level='READ COMMITTED', pool_size=20, max_overflow=0)
    sql = '\n'.join(Path(__file__).parents[1].joinpath('migrations/' + name).read_text() for name in ['001_quotas.sql', '005_parse_totals.sql'])
    with engine.begin() as conn:
        for statement in sql.split(';'):
            # SQL comments contain no semicolons in this migration.
            if statement.strip():
                conn.exec_driver_sql(statement)
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            for table in ('app_parse_totals', 'share_invites', 'quota_logs', 'parse_requests', 'daily_quotas', 'users'):
                conn.exec_driver_sql('DROP TABLE ' + table)
        engine.dispose()


@pytest.fixture
def quotas(db):
    with db.begin() as conn:
        for table in ('app_parse_totals', 'share_invites', 'quota_logs', 'parse_requests', 'daily_quotas', 'users'):
            conn.exec_driver_sql('DELETE FROM ' + table)
    return Quotas(Settings('mysql+pymysql://unused', frozenset([WHO.appid, 'wxOtherApp'])), db)


def scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def test_startup_requires_private_ingress_and_configuration(monkeypatch):
    monkeypatch.delenv('WECHAT_PRIVATE_INGRESS_ONLY', raising=False)
    with pytest.raises(RuntimeError, match='公网'):
        with TestClient(create_app()):
            pass
    monkeypatch.setenv('WECHAT_PRIVATE_INGRESS_ONLY', 'true')
    monkeypatch.setenv('DATABASE_URL', 'sqlite://')
    with pytest.raises(RuntimeError, match='mysql'):
        Settings.from_env()


def test_beijing_boundary():
    # Stable UTC+8 even when the host's local timezone differs.
    with patch('app.quota.datetime') as clock:
        clock.now.return_value = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        assert quota_day() == date(2026, 10, 19)
        assert clock.now.call_args.args[0].utcoffset(None).total_seconds() == 28800


def test_first_visit_identity_case_and_appid(quotas):
    assert quotas.balance(WHO)['remaining'] == 5
    assert quotas.balance(WHO)['remaining'] == 5
    quotas.balance(Identity(WHO.appid, 'openida'))
    quotas.balance(Identity('wxOtherApp', WHO.openid))
    assert scalar(quotas.engine, 'SELECT COUNT(*) FROM users') == 3
    assert scalar(quotas.engine, 'SELECT COUNT(*) FROM quota_logs') == 3


def test_success_multi_image_replay_payload_conflict(quotas):
    calls = []
    def parser(url):
        calls.append(url)
        return GOOD
    request_id = 'one-success-request'
    first = quotas.parse(WHO, request_id, URL, parser)
    assert first[0] == 200 and first[1]['quota']['remaining'] == 4
    assert quotas.parse(WHO, request_id, URL, parser) == first
    assert calls == [URL]
    with pytest.raises(HTTPException) as exc:
        quotas.parse(WHO, request_id, URL + 'changed', parser)
    assert exc.value.status_code == 409
    assert scalar(quotas.engine, "SELECT COUNT(*) FROM quota_logs WHERE amount=-1") == 1
    assert quotas.balance(WHO)['remaining'] == 4


@pytest.mark.parametrize('raw,result', [('no url', GOOD), (URL, {}), (URL, {'image_urls': []}),
                                       (URL, {'video_url': 'javascript:alert(1)'})])
def test_failed_parse_does_not_debit(quotas, raw, result):
    status, body = quotas.parse(WHO, 'failed-parse-request', raw, lambda _: result)
    assert status == 400 and body['success'] is False
    assert quotas.balance(WHO)['remaining'] == 5
    assert scalar(quotas.engine, "SELECT COUNT(*) FROM quota_logs WHERE amount=-1") == 0


def test_upstream_failure_is_persisted(quotas):
    def parser(_):
        raise RuntimeError('internal secret must not be in response')
    first = quotas.parse(WHO, 'upstream-failed-request', URL, parser)
    assert first[0] == 502 and 'secret' not in str(first)
    assert quotas.parse(WHO, 'upstream-failed-request', URL, lambda _: GOOD) == first
    assert quotas.balance(WHO)['remaining'] == 5


def test_concurrent_distinct_requests_never_overspend(quotas):
    def run(i):
        return quotas.parse(WHO, f'concurrent-request-{i}', URL, lambda _: (time.sleep(.02), GOOD)[1])[0]
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(run, range(12)))
    assert statuses.count(200) == 5
    assert statuses.count(429) == 7
    assert quotas.balance(WHO)['remaining'] == 0
    assert scalar(quotas.engine, "SELECT COUNT(*) FROM quota_logs WHERE amount=-1") == 5
    # Replays must still work with zero quota.
    assert quotas.parse(WHO, 'concurrent-request-0', URL, lambda _: GOOD)[0] in (200, 429)


def test_concurrent_same_request_parses_once(quotas):
    calls = []
    def parser(_):
        calls.append(1)
        time.sleep(.05)
        return GOOD
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: quotas.parse(WHO, 'same-concurrent-request', URL, parser), range(8)))
    assert all(result == results[0] for result in results)
    assert len(calls) == 1 and quotas.balance(WHO)['remaining'] == 4


def test_database_failure_rolls_back_debit_result_and_log(quotas):
    # Fail on log INSERT, after UPDATE remaining. The full transaction rolls back.
    from sqlalchemy import event
    def fail_log(conn, cursor, statement, parameters, context, executemany):
        if 'INSERT INTO quota_logs' in statement and "'parse_success'" in statement:
            raise OperationalError(statement, parameters, Exception('injected failure'))
    event.listen(quotas.engine, 'before_cursor_execute', fail_log)
    try:
        with pytest.raises(HTTPException) as exc:
            quotas.parse(WHO, 'database-failed-request', URL, lambda _: GOOD)
        assert exc.value.status_code == 503
    finally:
        event.remove(quotas.engine, 'before_cursor_execute', fail_log)
    assert scalar(quotas.engine, 'SELECT COUNT(*) FROM parse_requests') == 0
    assert quotas.balance(WHO)['remaining'] == 5
    assert quotas.parse(WHO, 'database-failed-request', URL, lambda _: GOOD)[0] == 200
    assert quotas.balance(WHO)['remaining'] == 4


def test_new_day_discards_leftover_and_old_replay_does_not_spend_today(quotas):
    with patch('app.quota.quota_day', return_value=date(2026, 10, 18)):
        old = quotas.parse(WHO, 'yesterday-request-1', URL, lambda _: GOOD)
    with patch('app.quota.quota_day', return_value=date(2026, 10, 19)):
        assert quotas.balance(WHO)['remaining'] == 5
        assert quotas.parse(WHO, 'yesterday-request-1', URL, lambda _: pytest.fail('no reparse')) == old
        assert quotas.balance(WHO)['remaining'] == 5
        quotas.parse(WHO, 'today-request-0001', URL, lambda _: GOOD)
        assert quotas.balance(WHO)['remaining'] == 4


def test_inflight_crossing_midnight_charges_starting_day(quotas):
    day = [date(2026, 10, 18)]
    def parser(_):
        day[0] = date(2026, 10, 19)
        return GOOD
    with patch('app.quota.quota_day', side_effect=lambda: day[0]):
        status, body = quotas.parse(WHO, 'midnight-inflight-request', URL, parser)
        assert status == 200 and body['quota']['quota_date'] == '2026-10-18'
        assert quotas.balance(WHO)['remaining'] == 5


def test_disabled_user(quotas):
    quotas.balance(WHO)
    with quotas.engine.begin() as conn:
        conn.execute(text("UPDATE users SET status='blocked'"))
    with pytest.raises(HTTPException) as exc:
        quotas.parse(WHO, 'disabled-user-request', URL, lambda _: GOOD)
    assert exc.value.status_code == 403


def test_routes_forbid_body_identity_and_unsafe_mutations(quotas):
    app = create_app(quotas.settings, quotas.engine, lambda _: GOOD)
    with TestClient(app) as client:
        assert client.get('/api/v1/quota').status_code == 401
        assert client.get('/api/v1/quota', headers={**HEADERS, 'X-WX-APPID': 'untrusted'}).status_code == 403
        assert client.get('/api/v1/quota', headers=[*HEADERS.items(), ('X-WX-OPENID', 'forged')]).status_code == 401
        payload = {'request_id': 'api-parse-request1', 'url': URL}
        assert client.post('/api/v1/parse', headers=HEADERS, json={**payload, 'openid': 'forged'}).status_code == 422
        assert client.post('/api/v1/parse', headers=HEADERS, json={'url': URL}).status_code == 422
        assert client.post('/api/v1/parse', headers=HEADERS, json=payload).status_code == 200
        assert client.get('/api/v1/quota', headers=HEADERS).json()['quota']['remaining'] == 4
        assert client.post('/api/v1/parse/batch', headers=HEADERS, json={'urls': [URL]}).status_code == 410
        for path in ('/api/count', '/api/v1/quota/refund', '/api/v1/quota/add', '/api/v1/users/delete'):
            assert client.post(path, headers=HEADERS, json={'action': 'inc'}).status_code == 404


def test_daily_configuration_is_frozen_until_next_day(quotas):
    with patch('app.quota.quota_day', return_value=date(2026, 10, 18)):
        quotas.balance(WHO)
        changed = Quotas(Settings('mysql+pymysql://unused', frozenset([WHO.appid]), 2), quotas.engine)
        assert changed.balance(WHO)['initial_quota'] == 5
    with patch('app.quota.quota_day', return_value=date(2026, 10, 19)):
        assert changed.balance(WHO)['remaining'] == 2


def test_separate_connections_share_account_lock(quotas):
    other_engine = create_engine(quotas.engine.url, isolation_level='READ COMMITTED')
    other = Quotas(quotas.settings, other_engine)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(lambda i: (quotas if i % 2 else other).parse(
                WHO, f'separate-instance-{i}', URL, lambda _: GOOD)[0], range(8)))
        assert statuses.count(200) == 5 and statuses.count(429) == 3
        assert quotas.balance(WHO)['remaining'] == 0
    finally:
        other_engine.dispose()


def test_schema_engines_and_invite_uniqueness(quotas):
    from sqlalchemy.exc import IntegrityError
    quotas.balance(WHO)
    quotas.balance(Identity(WHO.appid, 'friend'))
    with quotas.engine.connect() as conn:
        tables = conn.execute(text("SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")).all()
        assert len(tables) == 6 and all(engine == 'InnoDB' and collation == 'utf8mb4_bin' for engine, collation in tables)
        ids = list(conn.execute(text('SELECT id FROM users ORDER BY id')).scalars())
    sql = text("INSERT INTO share_invites (invite_code, inviter_id, invitee_id, campaign_id, created_at) VALUES (:code,:owner,:friend,'campaign',UTC_TIMESTAMP(6))")
    with quotas.engine.begin() as conn:
        conn.execute(sql, dict(code='CaseSensitive', owner=ids[0], friend=ids[1]))
        conn.execute(sql, dict(code='casesensitive', owner=ids[0], friend=None))
    # One effective relationship per campaign, even when the invite code differs.
    with pytest.raises(IntegrityError):
        with quotas.engine.begin() as conn:
            conn.execute(sql, dict(code='different', owner=ids[0], friend=ids[1]))
    with pytest.raises(IntegrityError):
        with quotas.engine.begin() as conn:
            conn.execute(sql, dict(code='CaseSensitive', owner=ids[0], friend=None))


def test_local_entry_uses_mysql_and_cannot_impersonate_wechat(quotas):
    from local_dev import create_local_app
    app = create_local_app(quotas.engine.url.render_as_string(hide_password=False), quotas.engine, lambda _: GOOD)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/v1/quota').json()['quota']['remaining'] == 5
        payload = {'request_id': 'local-test-parse-0001', 'url': URL}
        assert client.post('/api/v1/parse', json=payload).status_code == 200
        assert client.get('/api/v1/quota').json()['quota']['remaining'] == 4
        assert client.get('/api/v1/quota', headers=HEADERS).status_code == 400
    with TestClient(app, client=('192.168.1.50', 50000)) as client:
        assert client.get('/api/v1/quota').status_code == 403


def test_jianying_paid_preview_does_not_debit(quotas):
    from app.parsers.jianying import JianyingPreviewOnlyError
    def parser(url):
        raise JianyingPreviewOnlyError('暂未获取到无水印视频源，本次不扣解析次数。')
    before = quotas.balance(WHO)['remaining']
    status, response = quotas.parse(WHO, 'jianying-preview-test', URL, parser)
    assert status == 422
    assert response['error_code'] == 'WATERMARK_FREE_UNAVAILABLE'
    assert quotas.balance(WHO)['remaining'] == before
    assert scalar(quotas.engine, "SELECT COUNT(*) FROM quota_logs WHERE amount=-1") == 0


def test_total_counts_all_users_once_and_isolates_apps(quotas):
    assert quotas.balance(WHO)['total_parses'] == 0
    first = quotas.parse(WHO, 'total-first-request', URL, lambda _: GOOD)
    assert first[1]['total_parses'] == 1
    assert first[1]['quota']['total_parses'] == 1
    assert quotas.parse(WHO, 'total-first-request', URL, lambda _: GOOD) == first
    other = Identity(WHO.appid, 'total-other-user')
    assert quotas.parse(other, 'total-other-request', URL, lambda _: GOOD)[1]['total_parses'] == 2
    failed = quotas.parse(other, 'total-failed-request', 'bad', lambda _: GOOD)
    assert failed[1]['total_parses'] == 2
    assert quotas.balance(WHO)['total_parses'] == 2
    assert quotas.balance(Identity('wxOtherApp', 'other-app'))['total_parses'] == 0


def test_total_concurrent_users_no_lost_increments(quotas):
    def run(i):
        who = Identity(WHO.appid, 'total-user-' + str(i))
        return quotas.parse(who, 'total-concurrent-request', URL, lambda _: GOOD)[1]['total_parses']
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(16)))
    assert sorted(results) == list(range(1, 17))
    assert quotas.balance(WHO)['total_parses'] == 16


def test_total_rolls_back_when_persisting_result_fails(quotas):
    from sqlalchemy import event
    def fail_result(conn, cursor, statement, parameters, context, executemany):
        if 'UPDATE parse_requests SET status=' in statement:
            raise OperationalError(statement, parameters, Exception('injected after counter increment'))
    event.listen(quotas.engine, 'before_cursor_execute', fail_result)
    try:
        with pytest.raises(HTTPException):
            quotas.parse(WHO, 'total-rollback-request', URL, lambda _: GOOD)
    finally:
        event.remove(quotas.engine, 'before_cursor_execute', fail_result)
    assert quotas.balance(WHO)['total_parses'] == 0
    assert quotas.balance(WHO)['remaining'] == 5
    assert quotas.parse(WHO, 'total-rollback-request', URL, lambda _: GOOD)[1]['total_parses'] == 1


def test_total_migration_backfills_history_without_double_counting(quotas):
    quotas.parse(WHO, 'total-history-request', URL, lambda _: GOOD)
    quotas.parse(WHO, 'total-history-failed', 'bad', lambda _: GOOD)
    sql = Path(__file__).parents[1].joinpath('migrations/005_parse_totals.sql').read_text()
    with quotas.engine.begin() as conn:
        conn.exec_driver_sql('DELETE FROM app_parse_totals')
        for _ in range(2):
            for statement in sql.split(';'):
                if statement.strip(): conn.exec_driver_sql(statement)
    assert quotas.balance(WHO)['total_parses'] == 1
    assert quotas.parse(WHO, 'total-history-next', URL, lambda _: GOOD)[1]['total_parses'] == 2
