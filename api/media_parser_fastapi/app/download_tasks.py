"""Private-ingress download jobs with shared MySQL status and owner isolation."""
import hashlib
import json
import logging
import threading
import time
from uuid import uuid4, UUID
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .cloud_transfer import TransferSettings, transfer_file
from .identity import trusted_identity
from .transfer_http import TransferError, validate_url

logger = logging.getLogger('uvicorn.error.transfer')
TTL = 3600
# Longer than the worker deadline plus the maximum in-flight network operations.
STALE_AFTER = 900


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=32768)


class DatabaseTasks:
    def __init__(self, engine):
        self.engine = engine

    def create(self, task):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            with self.engine.begin() as conn:
                # Release abandoned jobs after a container restart. Unique active
                # owner keys enforce one active task across every service replica.
                conn.execute(text('UPDATE download_tasks SET active_owner_key=NULL '
                    'WHERE active_owner_key=:owner AND created_at<:stale'),
                    dict(owner=task['owner'], stale=now - timedelta(seconds=STALE_AFTER)))
                conn.execute(text('INSERT INTO download_tasks '
                    '(task_id, owner_key, active_owner_key, status, result_json, created_at, updated_at, expires_at) '
                    "VALUES (:id, :owner, :owner, 'PENDING', :result, :now, :now, :expiry)"),
                    dict(id=task['task_id'], owner=task['owner'], result=json.dumps(task),
                         now=now, expiry=now + timedelta(seconds=TTL)))
        except IntegrityError:
            raise HTTPException(429, detail='已有文件正在转存，请等待完成后再试') from None

    def reusable(self, owner, source_key, seconds):
        if not seconds:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.engine.connect() as conn:
            values = conn.execute(text("""SELECT result_json FROM download_tasks
                WHERE owner_key=:owner AND status='SUCCESS' AND expires_at>:now
                  AND updated_at>:since ORDER BY created_at DESC LIMIT 20"""),
                dict(owner=owner, now=now, since=now-timedelta(seconds=seconds))).scalars().all()
        for value in values:
            task = json.loads(value) if isinstance(value, str) else value
            if task.get('source_key') == source_key:
                return task
        return None

    def get(self, task_id):
        with self.engine.connect() as conn:
            value = conn.execute(text('SELECT result_json FROM download_tasks '
                'WHERE task_id=:id AND expires_at>:now'),
                dict(id=task_id, now=datetime.now(timezone.utc).replace(tzinfo=None))).scalar()
        return json.loads(value) if isinstance(value, str) else value

    def update(self, task):
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE download_tasks SET status=:status, result_json=:result, '
                'updated_at=:now WHERE task_id=:id'),
                dict(status=task['status'], result=json.dumps(task),
                     now=datetime.now(timezone.utc).replace(tzinfo=None), id=task['task_id']))

    def release(self, task):
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE download_tasks SET active_owner_key=NULL WHERE task_id=:id'),
                         dict(id=task['task_id']))

    def close(self):
        pass  # Engine lifetime is owned by the existing quota service.


def owner_key(identity):
    return hashlib.sha256((identity.appid + ':' + identity.openid).encode()).hexdigest()


class DownloadTasks:
    def __init__(self, settings, engine=None, store=None, transfer=None):
        self.settings = settings
        self.store = store if store is not None else (DatabaseTasks(engine) if settings.enabled else None)
        self.transfer = transfer or transfer_file
        self.slots = threading.BoundedSemaphore(4)

    def submit(self, url, identity, background):
        if not self.settings.enabled:
            raise HTTPException(503, detail='云存储转存尚未配置，请联系管理员')
        try:
            url = validate_url(url)
        except TransferError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        owner = owner_key(identity)
        # Include signed query parameters and storage target; never share an
        # owner's cloud file with a different user or persist the signed URL.
        source_key = hashlib.sha256(json.dumps(
            [url, self.settings.env, self.settings.region, self.settings.bucket],
            separators=(',', ':')).encode()).hexdigest()
        try:
            cached = self.store.reusable(owner, source_key, self.settings.cache_seconds)
        except Exception:
            cached = None  # Cache failure must not prevent a fresh transfer.
        if cached:
            logger.info('Transfer cache hit task=%s', cached['task_id'])
            return {'code': 0, 'task_id': cached['task_id']}
        if not self.slots.acquire(blocking=False):
            raise HTTPException(429, detail='转存任务繁忙，请稍后重试')
        task = {'task_id': uuid4().hex, 'status': 'PENDING',
                'owner': owner, 'source_key': source_key, 'created_at': time.time()}
        try:
            self.store.create(task)
        except Exception as exc:
            self.slots.release()
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(503, detail='任务数据库暂不可用，请稍后重试') from None
        background.add_task(self.run, task, url, identity)
        return {'code': 0, 'task_id': task['task_id']}

    def run(self, task, url, identity):
        try:
            task['status'] = 'PROCESSING'
            self.store.update(task)
            result = self.transfer(url, self.settings, identity=identity)
            task.update(result, status='SUCCESS')
        except Exception as exc:
            # Do not leak signed source URLs, COS authorization or API keys.
            logger.warning('Download task %s failed (%s)', task['task_id'], type(exc).__name__)
            task.update(status='FAILED', msg=str(exc) if isinstance(exc, TransferError)
                        else '文件下载或云存储上传失败，请重试或联系管理员')
        finally:
            try:
                self.store.update(task)
                self.store.release(task)
            except Exception:
                logger.error('Download task %s status could not be saved', task['task_id'])
            self.slots.release()

    def status(self, task_id, identity):
        if not self.settings.enabled:
            raise HTTPException(503, detail='云存储转存尚未配置，请联系管理员')
        try:
            normalized = UUID(task_id).hex
        except (ValueError, AttributeError):
            raise HTTPException(400, detail='task_id 无效') from None
        try:
            task = self.store.get(normalized)
        except Exception:
            raise HTTPException(503, detail='任务数据库暂不可用，请稍后重试') from None
        if not task or task['owner'] != owner_key(identity):
            raise HTTPException(404, detail='任务不存在或已过期，请重新提交')
        state = task['status']
        if state == 'SUCCESS':
            return {'status': 'success', **{key: task[key] for key in
                    ('fileID', 'contentType', 'extension', 'size') if key in task}}
        if state == 'FAILED':
            return {'status': 'failed', 'msg': task['msg']}
        if time.time() - task['created_at'] > STALE_AFTER:
            return {'status': 'failed', 'msg': '转存任务已超时或服务已重启，请重新提交'}
        return {'status': 'processing'}

    def close(self):
        if self.store is not None:
            self.store.close()


def add_download_routes(app):
    @app.post('/api/download/submit', tags=['download'])
    def submit(body: SubmitRequest, background: BackgroundTasks, request: Request,
               identity=Depends(trusted_identity)):
        return request.app.state.download_tasks.submit(body.url, identity, background)

    @app.get('/api/download/status', tags=['download'])
    def status(task_id: str, request: Request, identity=Depends(trusted_identity)):
        return request.app.state.download_tasks.status(task_id, identity)
