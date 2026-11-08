"""Authenticated, idempotent rewards for the mini-program's onClose callback.

This is client completion reporting, NOT a WeChat server-verified impression.
Only server-issued, account-bound sessions can credit the fixed five parses.
"""
import secrets
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .quota import quota_day, utcnow

REWARD = 5


class Ads:
    def __init__(self, quotas):
        self.quotas = quotas
        self.engine = quotas.engine

    def start(self, identity):
        unit = self.quotas.settings.rewarded_ad_unit_id
        if not unit:
            raise HTTPException(503, detail='激励视频广告暂未配置')
        try:
            with self.engine.begin() as conn:
                uid = self.quotas._account(conn, identity)
                day, now = quota_day(), utcnow()
                self.quotas._daily(conn, uid, day)
                args = dict(uid=uid, day=day, now=now, unit=unit)
                # A timeout/repeated tap reuses the pending session, under the
                # same account lock used by parses, daily grants and rewards.
                row = conn.execute(text('''SELECT session_id FROM ad_reward_sessions
                    WHERE user_id=:uid AND quota_date=:day AND ad_unit_id=:unit
                    AND completed_at IS NULL AND expires_at>:now
                    ORDER BY created_at DESC LIMIT 1'''), args).mappings().first()
                session_id = row['session_id'] if row else secrets.token_urlsafe(32)
                if not row:
                    conn.execute(text('''INSERT INTO ad_reward_sessions
                        (session_id,user_id,quota_date,ad_unit_id,created_at,expires_at)
                        VALUES (:sid,:uid,:day,:unit,:now,:expires)'''),
                        {**args, 'sid': session_id, 'expires': now + timedelta(minutes=15)})
            return {'session_id': session_id, 'ad_unit_id': unit, 'reward_amount': REWARD}
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='广告奖励服务暂不可用，请稍后重试') from exc

    def complete(self, identity, session_id):
        try:
            with self.engine.begin() as conn:
                uid = self.quotas._account(conn, identity)
                args = dict(uid=uid, sid=session_id)
                row = conn.execute(text('''SELECT quota_date, expires_at, completed_at
                    FROM ad_reward_sessions WHERE user_id=:uid AND session_id=:sid
                    FOR UPDATE'''), args).mappings().first()
                if not row:
                    raise HTTPException(404, detail='广告奖励记录无效')
                day, now = quota_day(), utcnow()
                if not row['completed_at'] and (row['expires_at'] <= now or row['quota_date'] != day):
                    raise HTTPException(410, detail='广告奖励已过期，请重新观看')
                quota = self.quotas._daily(conn, uid, day)
                duplicate = row['completed_at'] is not None
                if not duplicate:
                    args.update(day=day, now=now, reward=REWARD, key='ad:' + session_id,
                                balance=quota['remaining'] + REWARD)
                    conn.execute(text('''UPDATE daily_quotas SET remaining=remaining+:reward,
                        updated_at=:now WHERE user_id=:uid AND quota_date=:day'''), args)
                    conn.execute(text('''INSERT INTO quota_logs
                        (user_id,quota_date,event_type,amount,balance_after,business_key,created_at)
                        VALUES (:uid,:day,'ad_reward',:reward,:balance,:key,:now)'''), args)
                    conn.execute(text('''UPDATE ad_reward_sessions SET completed_at=:now
                        WHERE user_id=:uid AND session_id=:sid'''), args)
                    quota['remaining'] += REWARD
                result = {'reward_amount': 0 if duplicate else REWARD, 'duplicate': duplicate, 'quota': quota}
            return result  # Commit before returning a receipt.
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='奖励领取失败，请重试，勿重复观看') from exc
