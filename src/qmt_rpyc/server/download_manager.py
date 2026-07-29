import threading
import uuid
import logging
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable

from qmt_rpyc.server.serializer import serialize

logger = logging.getLogger(__name__)

_MAX_COMPLETED = 200

_DOWNLOAD_PREFIXES = ("download_",)


def is_download_function(name: str) -> bool:
    return any(name.startswith(p) for p in _DOWNLOAD_PREFIXES)


@dataclass
class DownloadTask:
    task_id: str
    function_name: str
    status: str
    submitted_at: datetime
    completed_at: Optional[datetime] = None
    progress: Optional[dict] = None
    result: Optional[dict] = None
    error: Optional[str] = None

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "function_name": self.function_name,
            "status": self.status,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
        }


class DownloadTaskManager:
    def __init__(self, max_workers=2, max_completed=_MAX_COMPLETED):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if max_completed < 1:
            raise ValueError("max_completed must be at least 1")
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._tasks = {}
        self._lock = threading.Lock()
        self._max_completed = max_completed
        self._accepting = True

    def submit(self, func, function_name, has_progress=False, _args=(), **kwargs):
        task_id = str(uuid.uuid4())
        task = DownloadTask(
            task_id=task_id,
            function_name=function_name,
            status="started",
            submitted_at=datetime.now(),
        )

        with self._lock:
            if not self._accepting:
                task.status = "failed"
                task.error = "download manager is shutting down"
                task.completed_at = datetime.now()
                self._tasks[task_id] = task
                self._prune_locked()
                return task_id
            self._tasks[task_id] = task
            self._prune_locked()

        try:
            if has_progress:
                progress_cb = self._make_progress_callback(task_id)
                future = self._executor.submit(
                    self._run_with_progress, func, task_id, progress_cb, _args, **kwargs)
            else:
                future = self._executor.submit(
                    self._run_simple, func, task_id, _args, **kwargs)
        except RuntimeError as e:
            with self._lock:
                task.status = "failed"
                task.error = "submit failed: {}".format(e)
                task.completed_at = datetime.now()
                self._prune_locked()
            return task_id

        future.add_done_callback(lambda f: self._on_done(task_id, f))
        return task_id

    def get_task(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task else None

    def get_stats(self):
        with self._lock:
            counts = {
                "total": len(self._tasks),
                "started": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
            }
            for task in self._tasks.values():
                if task.status in counts:
                    counts[task.status] += 1
            return counts

    def shutdown(self):
        with self._lock:
            self._accepting = False
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _prune_locked(self):
        if len(self._tasks) <= self._max_completed:
            return
        terminal = [
            (t.submitted_at or datetime.min, tid)
            for tid, t in self._tasks.items()
            if t.status in ("completed", "failed")
        ]
        excess = len(self._tasks) - self._max_completed
        if not terminal:
            return
        terminal.sort()
        for _, tid in terminal[:excess]:
            self._tasks.pop(tid, None)

    def _make_progress_callback(self, task_id):
        def on_progress(data):
            try:
                if not isinstance(data, dict):
                    raise TypeError(
                        "download progress must be a dict, got "
                        + type(data).__name__)
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task:
                        task.status = "running"
                        task.progress = {
                            "total": data.get("total"),
                            "finished": data.get("finished"),
                            "stockcode": data.get("stockcode", ""),
                            "message": data.get("message", ""),
                        }
            except Exception:
                logger.warning(
                    "invalid progress callback for task %s",
                    task_id,
                    exc_info=True,
                )
        return on_progress

    def _run_simple(self, func, task_id, _args=(), **kwargs):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "running"
        result = func(*_args, **kwargs)
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.result = serialize(result) if result is not None else None

    def _run_with_progress(self, func, task_id, progress_callback, _args=(), **kwargs):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "running"
        result = func(callback=progress_callback, *_args, **kwargs)
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.result = serialize(result) if result is not None else None

    def _on_done(self, task_id, future):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            try:
                if future.cancelled():
                    raise CancelledError("download cancelled during shutdown")
                exc = future.exception()
                if exc:
                    task.status = "failed"
                    task.error = str(exc)
                else:
                    task.status = "completed"
            except Exception as e:
                task.status = "failed"
                task.error = str(e) or type(e).__name__
            task.completed_at = datetime.now()
            self._prune_locked()
