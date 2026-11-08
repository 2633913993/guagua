"""Authenticated share visits and exactly-once MySQL rewards.

Only the invitation token comes from the client. Visitor/list owner identities
come from the existing trusted_identity dependency, never from request fields.
"""
import secrets
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from .quota import BEIJING, quota_day, utcnow

CAMPAIGN = 'share-v1'


def display_time(value):
    # MySQL timestamps are stored as naive UTC, serialized with an explicit zone.
    from datetime import timezone
    return value.replace(tzinfo=timezone.utc).astimezone(BEIJING).isoformat()


class Sharing:
    def __init__(self, quotas):
        self.quotas = quotas
        self.engine = quotas.engine
        self.settings = quotas.settings

    def profile(self, identity):
        try:
            with self.engine.begin() as conn:
                uid = self.quotas._account(conn, identity)
                token = conn.execute(text('SELECT invite_token FROM share_links WHERE user_id=:uid'), {'uid': uid}).scalar()
                if not token:
                    token = secrets.token_urlsafe(32)
                    conn.execute(text('''INSERT INTO share_links (user_id, invite_token, created_at)
                        VALUES (:uid, :token, :now)'''), dict(uid=uid, token=token, now=utcnow()))
                return {'inviter': token, 'reward_per_friend': self.settings.share_reward,
                        'campaign': CAMPAIGN, 'reward_rule': 'every_entry'}
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='分享服务暂不可用，请稍后重试') from exc

    def record(self, identity, token, source, entry_id):
        try:
            # Resolve token and establish the visitor before taking pair locks.
            # Each transaction below locks user rows in ascending order, avoiding
            # deadlocks for simultaneous A->B and B->A invitations.
            with self.engine.connect() as conn:
                inviter = conn.execute(text('''SELECT u.id, u.appid FROM share_links s
                    JOIN users u ON u.id=s.user_id WHERE s.invite_token=:token'''), {'token': token}).mappings().first()
            if not inviter or inviter['appid'] != identity.appid:
                raise HTTPException(404, detail='邀请链接无效')
            owner = inviter['id']
            with self.engine.begin() as conn:
                visitor = self.quotas._account(conn, identity)
            if owner == visitor:
                return {'recorded': False, 'reason': 'self_invite', 'reward_amount': 0}
            with self.engine.begin() as conn:
                users = conn.execute(text('''SELECT id, status FROM users
                    WHERE id IN (:owner, :visitor) ORDER BY id FOR UPDATE'''),
                    dict(owner=owner, visitor=visitor)).mappings().all()
                if len(users) != 2 or any(user['status'] != 'active' for user in users):
                    raise HTTPException(403, detail='账号已停用')
                args = dict(owner=owner, visitor=visitor, source=source, now=utcnow())
                visit = conn.execute(text('''SELECT id, reward_amount FROM share_visits
                    WHERE inviter_id=:owner AND invitee_id=:visitor'''), args).mappings().first()
                day = quota_day()
                # A distinct app entry may reward again, including the same
                # friend. The persisted entry ID identifies a retry, not a user.
                claimed = conn.execute(text("""SELECT inviter_id, invitee_id FROM share_invites
                    WHERE invite_code=:entry_id"""), {'entry_id': entry_id}).mappings().first()
                if claimed:
                    if claimed['inviter_id'] != owner or claimed['invitee_id'] != visitor:
                        raise HTTPException(409, detail='进入编号已用于其他邀请请求')
                    return {'recorded': True, 'duplicate': True, 'reward_amount': 0}
                reward = self.settings.share_reward
                conn.execute(text("""INSERT INTO share_invites
                    (invite_code, inviter_id, invitee_id, campaign_id, status, reward_date, created_at)
                    VALUES (:code,:owner,:visitor,:campaign,:status,:day,:now)"""),
                    {**args, 'code': entry_id, 'campaign': f'{CAMPAIGN}:{entry_id}',
                     'status': 'rewarded' if reward else 'accepted', 'day': day if reward else None})
                if reward:
                    quota = self.quotas._daily(conn, owner, day)
                    conn.execute(text('''UPDATE daily_quotas SET remaining=remaining+:reward,
                        updated_at=:now WHERE user_id=:owner AND quota_date=:day'''),
                        {**args, 'reward': reward, 'day': day})
                    conn.execute(text('''INSERT INTO quota_logs
                        (user_id,quota_date,event_type,amount,balance_after,business_key,created_at)
                        VALUES (:owner,:day,'share_reward',:reward,:balance,:key,:now)'''),
                        {**args, 'day': day, 'reward': reward, 'balance': quota['remaining'] + reward,
                         'key': f'share:{entry_id}'})
                if visit:
                    conn.execute(text("""UPDATE share_visits SET last_seen_at=:now,
                        visit_count=visit_count+1, reward_amount=reward_amount+:reward
                        WHERE id=:id"""), {**args, 'reward': reward, 'id': visit['id']})
                else:
                    conn.execute(text("""INSERT INTO share_visits
                        (inviter_id,invitee_id,source,created_at,last_seen_at,reward_amount)
                        VALUES (:owner,:visitor,:source,:now,:now,:reward)"""), {**args, 'reward': reward})
                result = {'recorded': True, 'duplicate': bool(claimed), 'reward_amount': reward}
            # Transaction has committed before the success receipt is returned.
            return result
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='邀请记录暂不可用，请稍后重试') from exc

    def invitees(self, identity, before=0, limit=20):
        try:
            with self.engine.begin() as conn:
                uid = self.quotas._account(conn, identity)
                args = dict(uid=uid, before=before, limit=limit + 1)
                rows = conn.execute(text('''SELECT id, invitee_id, created_at, last_seen_at,
                    visit_count, reward_amount FROM share_visits WHERE inviter_id=:uid
                    AND (:before=0 OR id<:before) ORDER BY id DESC LIMIT :limit'''), args).mappings().all()
                total = conn.execute(text('SELECT COUNT(*) FROM share_visits WHERE inviter_id=:uid'), args).scalar_one()
                rewarded = conn.execute(text('SELECT COALESCE(SUM(reward_amount),0) FROM share_visits WHERE inviter_id=:uid'), args).scalar_one()
            items = [{'id': row['id'], 'visitor_name': f"好友 {row['invitee_id']}",
                      'created_at': display_time(row['created_at']),
                      'last_seen_at': display_time(row['last_seen_at']),
                      'visit_count': row['visit_count'], 'reward_amount': row['reward_amount']}
                     for row in rows[:limit]]
            return {'items': items, 'total': total, 'total_reward': int(rewarded),
                    'next_cursor': items[-1]['id'] if len(rows) > limit else None,
                    'reward_per_friend': self.settings.share_reward}
        except SQLAlchemyError as exc:
            raise HTTPException(503, detail='邀请记录暂不可用，请稍后重试') from exc
