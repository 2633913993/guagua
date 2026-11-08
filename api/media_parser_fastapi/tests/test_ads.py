from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text, event
from sqlalchemy.exc import OperationalError
from test_quotas import db as base_db
from app.ads import Ads
from app.identity import Identity
from app.main import create_app
from app.quota import Quotas, utcnow
from app.settings import Settings

A = Identity('wxTestApp', 'ad-viewer')
B = Identity('wxTestApp', 'other-viewer')


@pytest.fixture(scope='module')
def ad_db(base_db):
    sql = Path(__file__).parents[1].joinpath('migrations/004_ad_rewards.sql').read_text()
    with base_db.begin() as conn:
        for statement in sql.split(';'):
            if statement.strip(): conn.exec_driver_sql(statement)
    yield base_db
    with base_db.begin() as conn: conn.exec_driver_sql('DROP TABLE ad_reward_sessions')


@pytest.fixture
def ads(ad_db):
    with ad_db.begin() as conn:
        for table in ('ad_reward_sessions', 'share_invites', 'quota_logs', 'parse_requests', 'daily_quotas', 'users'):
            conn.exec_driver_sql('DELETE FROM ' + table)
    return Ads(Quotas(Settings('mysql+pymysql://unused', frozenset([A.appid]),
                              rewarded_ad_unit_id='adunit-test'), ad_db))


def test_unconfigured_fails_closed(ads):
    ads.quotas.settings = replace(ads.quotas.settings, rewarded_ad_unit_id='')
    with pytest.raises(HTTPException) as exc: ads.start(A)
    assert exc.value.status_code == 503
    assert ads.quotas.balance(A)['remaining'] == 5


def test_every_completed_video_adds_five_even_with_positive_balance(ads):
    assert ads.quotas.balance(A)['remaining'] == 5
    first = ads.start(A)['session_id']
    assert ads.complete(A, first)['quota']['remaining'] == 10
    second = ads.start(A)['session_id']
    assert second != first
    assert ads.complete(A, second)['quota']['remaining'] == 15
    assert ads.complete(A, first)['duplicate'] is True
    assert ads.quotas.balance(A)['remaining'] == 15


def test_unfinished_video_does_not_credit(ads):
    first = ads.start(A)
    assert ads.start(A) == first
    assert ads.quotas.balance(A)['remaining'] == 5


def test_account_binding_and_forged_session(ads):
    sid = ads.start(A)['session_id']
    for who, token in [(B, sid), (A, 'X'*43)]:
        with pytest.raises(HTTPException) as exc: ads.complete(who, token)
        assert exc.value.status_code == 404
    assert ads.quotas.balance(A)['remaining'] == 5


def test_concurrent_duplicate_completions_credit_once(ads):
    sid = ads.start(A)['session_id']
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ads.complete(A, sid), range(8)))
    assert sum(r['reward_amount'] for r in results) == 5
    assert ads.quotas.balance(A)['remaining'] == 10
    with ads.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM quota_logs WHERE event_type='ad_reward'").scalar_one() == 1


def test_expiry_and_day_boundary_do_not_credit(ads):
    sid = ads.start(A)['session_id']
    with patch('app.ads.utcnow', return_value=utcnow() + timedelta(minutes=16)):
        with pytest.raises(HTTPException) as exc: ads.complete(A, sid)
    assert exc.value.status_code == 410
    with patch('app.ads.quota_day', return_value=__import__('datetime').date.today() + timedelta(days=2)):
        with pytest.raises(HTTPException) as exc: ads.complete(A, sid)
    assert exc.value.status_code == 410
    assert ads.quotas.balance(A)['remaining'] == 5


def test_completed_retry_after_expiry_stays_idempotent(ads):
    sid = ads.start(A)['session_id']
    ads.complete(A, sid)
    with patch('app.ads.utcnow', return_value=utcnow() + timedelta(minutes=16)):
        assert ads.complete(A, sid)['reward_amount'] == 0
    assert ads.quotas.balance(A)['remaining'] == 10


def test_credit_and_ledger_roll_back_together_then_retry(ads):
    sid = ads.start(A)['session_id']
    def fail(conn, cursor, statement, parameters, context, executemany):
        if "'ad_reward'" in statement:
            raise OperationalError(statement, parameters, Exception('injected failure'))
    event.listen(ads.engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(HTTPException) as exc: ads.complete(A, sid)
        assert exc.value.status_code == 503
    finally: event.remove(ads.engine, 'before_cursor_execute', fail)
    assert ads.quotas.balance(A)['remaining'] == 5
    assert ads.complete(A, sid)['quota']['remaining'] == 10


def test_routes_reject_client_amount_identity_and_unauthenticated_requests(ads):
    headers = {'X-WX-APPID': A.appid, 'X-WX-OPENID': A.openid}
    with TestClient(create_app(ads.quotas.settings, ads.engine)) as client:
        assert client.post('/api/ads/start', json={}).status_code == 401
        assert client.post('/api/ads/start', headers=headers, json={'openid':'fake'}).status_code == 422
        sid = client.post('/api/ads/start', headers=headers, json={}).json()['data']['session_id']
        assert client.post('/api/ads/complete', headers=headers, json={'session_id':sid,'amount':999}).status_code == 422
        assert client.post('/api/ads/complete', headers=headers, json={'session_id':sid}).json()['data']['quota']['remaining'] == 10


def test_zero_balance_reward_then_parse_consumes_one(ads):
    with ads.engine.begin() as conn:
        ads.quotas._daily(conn, ads.quotas._account(conn, A), __import__('app.quota', fromlist=['quota_day']).quota_day())
        conn.exec_driver_sql('UPDATE daily_quotas SET remaining=0')
    sid = ads.start(A)['session_id']
    assert ads.complete(A, sid)['quota']['remaining'] == 5
    status, body = ads.quotas.parse(A, 'after-ad-parse-request', 'https://v.douyin.com/test/', lambda _: {'video_url':'https://cdn.example/v.mp4'})
    assert status == 200 and body['quota']['remaining'] == 4
