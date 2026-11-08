"""Per-account serialization: request, debit, log and result share one transaction.

The user row stays locked during parsing. A crash rolls everything back. This
trades same-account concurrency for simple, durable exactly-once consumption.
No in-memory locks, reservations, refunds or externally callable credit methods.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException

from .parsers.common import extract_urls, clean_url
from .parsers.jianying import JianyingPreviewOnlyError

logger = logging.getLogger(__name__)
BEIJING = timezone(timedelta(hours=8))


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def quota_day():
    return datetime.now(BEIJING).date()


def usable_media(result):
    if not isinstance(result, dict):
        return False
    candidates = [result.get('video_url')]
    if isinstance(result.get('image_urls'), list):
        candidates += result['image_urls']
    for value in candidates:
        if not isinstance(value, str) or any(c.isspace() for c in value):
            continue
        try:
            parts = urlsplit(value)
            if parts.scheme in ('http', 'https') and parts.hostname:
                return True
        except ValueError:
            pass
    return False


class Quotas:
    def __init__(self, settings, engine=None):
        self.settings = settings
        self.engine = engine or create_engine(
            settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10,
            isolation_level='READ COMMITTED',
            connect_args={'connect_timeout': 5, 'read_timeout': 180, 'write_timeout': 10},
        )

    def _account(self, conn, identity):
        now = utcnow()
        conn.execute(text('''INSERT INTO users (appid, openid, created_at, last_seen_at)
            VALUES (:appid, :openid, :now, :now)
            ON DUPLICATE KEY UPDATE last_seen_at=:now'''),
            dict(appid=identity.appid, openid=identity.openid, now=now))
        user = conn.execute(text('''SELECT id, status FROM users
            WHERE appid=:appid AND openid=:openid FOR UPDATE'''),
            dict(appid=identity.appid, openid=identity.openid)).mappings().one()
        if user['status'] != 'active':
            raise HTTPException(403, detail='账号已停用')
        return user['id']

    def _daily(self, conn, uid, day):
        args = dict(uid=uid, day=day, initial=self.settings.daily_free_quota, now=utcnow())
        inserted = conn.execute(text('''INSERT IGNORE INTO daily_quotas
            (user_id, quota_date, initial_quota, remaining, created_at, updated_at)
            VALUES (:uid, :day, :initial, :initial, :now, :now)'''), args).rowcount
        row = conn.execute(text('''SELECT initial_quota, remaining FROM daily_quotas
            WHERE user_id=:uid AND quota_date=:day FOR UPDATE'''), args).mappings().one()
        if inserted:
            conn.execute(text('''INSERT INTO quota_logs
                (user_id, quota_date, event_type, amount, balance_after, business_key, created_at)
                VALUES (:uid, :day, 'daily_grant', :initial, :initial, :key, :now)'''),
                {**args, 'key': 'daily:' + day.isoformat()})
        return dict(quota_date=day.isoformat(), initial_quota=row['initial_quota'],
                    remaining=row['remaining'], server_time=datetime.now(BEIJING).isoformat())

    def _total_parses(self, conn, appid):
        value = conn.execute(text('SELECT total_parses FROM app_parse_totals WHERE appid=:appid'),
                             {'appid': appid}).scalar_one_or_none()
        return int(value or 0)

    def balance(self, identity):
        try:
            with self.engine.begin() as conn:
                uid = self._account(conn, identity)
                quota = self._daily(conn, uid, quota_day())
                quota['total_parses'] = self._total_parses(conn, identity.appid)
                return quota
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='次数服务暂不可用，请稍后重试') from exc

    def parse(self, identity, request_id, raw, parser):
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        try:
            with self.engine.begin() as conn:
                uid = self._account(conn, identity)
                # Read after acquiring the account lock: a waiter crossing midnight
                # starts a new day. An active parser retains its starting day.
                day = quota_day()
                args = dict(uid=uid, request_id=request_id)
                old = conn.execute(text('''SELECT payload_hash, result_json FROM parse_requests
                    WHERE user_id=:uid AND request_id=:request_id'''), args).mappings().first()
                if old:
                    if old['payload_hash'] != digest:
                        raise HTTPException(409, detail='request_id 已用于不同的解析内容')
                    response = old['result_json']
                    if isinstance(response, str):
                        response = json.loads(response)
                    body = response['body']
                    # Historical durable responses predate the statistics field.
                    if 'total_parses' not in body:
                        body['total_parses'] = self._total_parses(conn, identity.appid)
                        if isinstance(body.get('quota'), dict):
                            body['quota']['total_parses'] = body['total_parses']
                    return response['http_status'], body
                quota = self._daily(conn, uid, day)
                now = utcnow()
                args.update(day=day, digest=digest, now=now)
                conn.execute(text('''INSERT INTO parse_requests
                    (user_id, request_id, payload_hash, quota_date, status, started_at)
                    VALUES (:uid, :request_id, :digest, :day, 'processing', :now)'''), args)
                code = None
                status = 200
                result = None
                if quota['remaining'] < 1:
                    code, status, detail = 'QUOTA_EXHAUSTED', 429, '今日解析次数已用完'
                else:
                    urls = extract_urls(raw)
                    if not urls:
                        code, status, detail = 'INVALID_URL', 400, '未找到有效的 http/https 链接'
                    else:
                        try:
                            result = parser(clean_url(urls[0]))
                            if not usable_media(result):
                                raise ValueError('未解析到有效图片或视频')
                            # Validate serializability before making any debit.
                            json.dumps(result, ensure_ascii=False, allow_nan=False)
                        except JianyingPreviewOnlyError as exc:
                            code, status, detail = 'WATERMARK_FREE_UNAVAILABLE', 422, str(exc)
                        except ValueError:
                            code, status, detail = 'PARSE_FAILED', 400, '链接无效或未解析到有效图片或视频'
                        except Exception:
                            logger.warning('Media parser failed', exc_info=True)
                            code, status, detail = 'UPSTREAM_ERROR', 502, '解析服务暂不可用，请稍后重试'
                if code is None:
                    updated = conn.execute(text('''UPDATE daily_quotas SET remaining=remaining-1,
                        updated_at=:now WHERE user_id=:uid AND quota_date=:day AND remaining>0'''), args)
                    if updated.rowcount != 1:
                        raise RuntimeError('Quota invariant violated')
                    quota['remaining'] -= 1
                    conn.execute(text('''INSERT INTO quota_logs
                        (user_id, quota_date, event_type, amount, balance_after, business_key, created_at)
                        VALUES (:uid, :day, 'parse_success', -1, :balance, :key, :now)'''),
                        {**args, 'balance': quota['remaining'], 'key': 'parse:' + request_id})
                    # Keep the global lock only for this short final transaction section.
                    # Counter, debit and durable response commit or roll back together.
                    conn.execute(text("""INSERT INTO app_parse_totals (appid, total_parses)
                        VALUES (:appid, 1) ON DUPLICATE KEY UPDATE total_parses=total_parses+1"""),
                        {'appid': identity.appid})
                    total = self._total_parses(conn, identity.appid)
                    quota['total_parses'] = total
                    body = dict(success=True, data=result, request_id=request_id, quota=quota,
                                total_parses=total)
                else:
                    total = self._total_parses(conn, identity.appid)
                    quota['total_parses'] = total
                    body = dict(success=False, detail=detail, error_code=code,
                                request_id=request_id, quota=quota, total_parses=total)
                conn.execute(text('''UPDATE parse_requests SET status=:status, result_json=:response,
                    error_code=:code, finished_at=:finished WHERE user_id=:uid AND request_id=:request_id'''),
                    {**args, 'status': 'succeeded' if code is None else 'failed', 'code': code,
                     'finished': utcnow(), 'response': json.dumps(
                         {'http_status': status, 'body': body}, ensure_ascii=False, allow_nan=False)})
            # Respond only after a successful commit. On ambiguous connection loss,
            # the client retries the same request_id and reads the durable result.
            return status, body
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='次数服务暂不可用，请保留原请求重试') from exc
