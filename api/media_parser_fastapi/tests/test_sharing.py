from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from unittest.mock import patch
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text, event
from sqlalchemy.exc import OperationalError
from test_quotas import db as base_db
from app.identity import Identity
from app.main import create_app
from app.quota import Quotas
from app.settings import Settings
from app.sharing import Sharing

A = Identity('wxTestApp', 'share-owner')
B = Identity('wxTestApp', 'share-friend')
C = Identity('wxTestApp', 'other-owner')
SOURCE = 'wechat_share'


@pytest.fixture(scope='module')
def share_db(base_db):
    sql = Path(__file__).parents[1].joinpath('migrations/002_sharing.sql').read_text()
    with base_db.begin() as conn:
        for statement in sql.split(';'):
            if statement.strip(): conn.exec_driver_sql(statement)
    yield base_db
    with base_db.begin() as conn:
        conn.exec_driver_sql('DROP TABLE share_visits')
        conn.exec_driver_sql('DROP TABLE share_links')


@pytest.fixture
def sharing(share_db):
    with share_db.begin() as conn:
        for table in ('share_visits', 'share_links', 'share_invites', 'quota_logs', 'parse_requests', 'daily_quotas', 'users'):
            conn.exec_driver_sql('DELETE FROM ' + table)
    return Sharing(Quotas(Settings('mysql+pymysql://unused', frozenset(['wxTestApp', 'wxOtherApp'])), share_db))


def token(service, who=A):
    return service.profile(who)['inviter']


def entry(service, visitor, code, event_id=None):
    return service.record(visitor, code, SOURCE, event_id or uuid.uuid4().hex)


def count(service, table):
    with service.engine.connect() as conn:
        return conn.exec_driver_sql('SELECT COUNT(*) FROM ' + table).scalar_one()


def test_profile_is_stable_and_does_not_expose_openid(sharing):
    profile = sharing.profile(A)
    assert profile == sharing.profile(A)
    assert len(profile['inviter']) == 43 and A.openid not in str(profile)
    assert profile['reward_per_friend'] == 5 and profile['reward_rule'] == 'every_entry'
    assert count(sharing, 'share_links') == 1


def test_repeat_open_rewards_again_but_network_retries_do_not(sharing):
    code = token(sharing)
    assert entry(sharing, A, code)['reason'] == 'self_invite'
    assert sharing.quotas.balance(A)['remaining'] == 5
    event_id = uuid.uuid4().hex
    assert entry(sharing, B, code, event_id)['reward_amount'] == 5
    assert sharing.quotas.balance(A)['remaining'] == 10
    assert sharing.quotas.balance(B)['remaining'] == 5
    assert entry(sharing, B, code, event_id)['reward_amount'] == 0
    assert sharing.quotas.balance(A)['remaining'] == 10
    assert entry(sharing, B, code)['reward_amount'] == 5
    assert sharing.quotas.balance(A)['remaining'] == 15
    assert count(sharing, 'share_invites') == 2
    listing = sharing.invitees(A)
    assert listing['total'] == 1 and listing['total_reward'] == 10
    assert listing['items'][0]['visit_count'] == 2
    assert listing['items'][0]['created_at'].endswith('+08:00')
    assert 'openid' not in str(listing)
    assert sharing.invitees(B)['items'] == []


def test_each_inviter_receives_their_own_entry_reward(sharing):
    entry(sharing, B, token(sharing, A))
    result = entry(sharing, B, token(sharing, C))
    assert result['recorded'] and result['reward_amount'] == 5
    assert sharing.invitees(C)['total'] == 1
    assert sharing.quotas.balance(C)['remaining'] == 10


def test_concurrent_retries_of_one_entry_pay_once(sharing):
    code, event_id = token(sharing), uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: entry(sharing, B, code, event_id), range(8)))
    assert sum(r['reward_amount'] for r in receipts) == 5
    assert sharing.quotas.balance(A)['remaining'] == 10
    assert count(sharing, 'share_invites') == 1
    assert count(sharing, 'share_visits') == 1


def test_distinct_entries_and_cursor_pagination(sharing):
    code = token(sharing)
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda i: entry(sharing, Identity(A.appid, 'friend-' + str(i)), code), range(6)))
    assert sum(r['reward_amount'] for r in receipts) == 30
    assert sharing.quotas.balance(A)['remaining'] == 35
    first = sharing.invitees(A, limit=3)
    second = sharing.invitees(A, before=first['next_cursor'], limit=3)
    assert len(first['items']) == len(second['items']) == 3
    assert not set(r['id'] for r in first['items']) & set(r['id'] for r in second['items'])
    assert second['next_cursor'] is None


def test_reciprocal_invitations_do_not_deadlock(sharing):
    a, b = token(sharing, A), token(sharing, B)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(entry, sharing, B, a), pool.submit(entry, sharing, A, b)]
        assert all(f.result(timeout=10)['recorded'] for f in futures)
    assert sharing.quotas.balance(A)['remaining'] == sharing.quotas.balance(B)['remaining'] == 10


def test_failed_reward_rolls_back_visits_balance_and_ledger(sharing):
    code, event_id = token(sharing), uuid.uuid4().hex
    def fail(conn, cursor, statement, parameters, context, executemany):
        if 'INSERT INTO quota_logs' in statement and "'share_reward'" in statement:
            raise OperationalError(statement, parameters, Exception('injected failure'))
    event.listen(sharing.engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(HTTPException) as exc: entry(sharing, B, code, event_id)
        assert exc.value.status_code == 503
    finally: event.remove(sharing.engine, 'before_cursor_execute', fail)
    assert count(sharing, 'share_visits') == count(sharing, 'share_invites') == 0
    assert sharing.quotas.balance(A)['remaining'] == 5
    assert entry(sharing, B, code, event_id)['reward_amount'] == 5
    assert sharing.quotas.balance(A)['remaining'] == 10


def test_invalid_cross_app_and_disabled_accounts(sharing):
    with pytest.raises(HTTPException) as exc: entry(sharing, B, 'x' * 43)
    assert exc.value.status_code == 404
    code = token(sharing)
    with pytest.raises(HTTPException) as exc: entry(sharing, Identity('wxOtherApp', B.openid), code)
    assert exc.value.status_code == 404
    with sharing.engine.begin() as conn:
        conn.execute(text("UPDATE users SET status='blocked' WHERE openid=:openid"), {'openid': A.openid})
    with pytest.raises(HTTPException) as exc: entry(sharing, B, code)
    assert exc.value.status_code == 403


def test_reward_expires_at_midnight_old_event_replays_do_not_grant_today(sharing):
    code, event_id = token(sharing), uuid.uuid4().hex
    with patch('app.sharing.quota_day', return_value=date(2026, 10, 18)), patch('app.quota.quota_day', return_value=date(2026, 10, 18)):
        entry(sharing, B, code, event_id)
        assert sharing.quotas.balance(A)['remaining'] == 10
    with patch('app.sharing.quota_day', return_value=date(2026, 10, 19)), patch('app.quota.quota_day', return_value=date(2026, 10, 19)):
        assert sharing.quotas.balance(A)['remaining'] == 5
        assert entry(sharing, B, code, event_id)['reward_amount'] == 0
        assert sharing.quotas.balance(A)['remaining'] == 5
        entry(sharing, B, code)
        assert sharing.quotas.balance(A)['remaining'] == 10


def test_zero_reward_records_without_minting(sharing):
    disabled = Sharing(Quotas(Settings('mysql+pymysql://unused', frozenset([A.appid]), 5, 0), sharing.engine))
    assert entry(disabled, B, token(disabled))['reward_amount'] == 0
    assert disabled.invitees(A)['total'] == 1
    assert disabled.quotas.balance(A)['remaining'] == 5


def test_entry_id_cannot_be_reused_for_another_visitor_or_inviter(sharing):
    code, event_id = token(sharing), uuid.uuid4().hex
    entry(sharing, B, code, event_id)
    with pytest.raises(HTTPException) as exc: entry(sharing, C, code, event_id)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc: entry(sharing, B, token(sharing, C), event_id)
    assert exc.value.status_code == 409


def test_routes_derive_visitor_and_list_owner_from_trusted_identity(sharing):
    headers = lambda who: {'X-WX-APPID':who.appid, 'X-WX-OPENID':who.openid}
    with TestClient(create_app(sharing.settings, sharing.engine)) as client:
        assert client.get('/api/share/profile').status_code == 401
        code = client.get('/api/share/profile', headers=headers(A)).json()['data']['inviter']
        payload = {'inviter':code, 'source':SOURCE, 'entry_id':uuid.uuid4().hex}
        assert client.post('/api/share/log', headers=headers(B), json={**payload, 'invitee': A.openid}).status_code == 422
        assert client.post('/api/share/log', headers=headers(B), json={**payload, 'amount':100}).status_code == 422
        assert client.post('/api/share/log', headers=headers(B), json={**payload, 'source':'forged'}).status_code == 422
        assert client.post('/api/share/log', headers=headers(B), json={'inviter':code}).status_code == 422
        assert client.post('/api/share/log', json=payload).status_code == 401
        assert client.post('/api/share/log', headers=headers(B), json=payload).json()['data']['reward_amount'] == 5
        assert client.post('/api/share/log', headers=headers(B), json=payload).json()['data']['reward_amount'] == 0
        assert client.get('/api/share/my-invitees?inviter_id=1', headers=headers(B)).json()['data']['total'] == 0
        assert client.get('/api/share/my-invitees?limit=51', headers=headers(A)).status_code == 422
        assert client.get('/api/share/my-invitees', headers=headers(A)).json()['data']['total_reward'] == 5
