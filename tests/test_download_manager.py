import time
import pytest
from qmt_rpyc.server.download_manager import DownloadTaskManager, DownloadTask, is_download_function


class TestIsDownloadFunction:
    def test_download_prefix(self):
        assert is_download_function("download_history_data") is True
        assert is_download_function("download_financial_data") is True

    def test_non_download(self):
        assert is_download_function("get_market_data") is False
        assert is_download_function("order_stock") is False


class TestDownloadTaskManager:
    def test_submit_returns_task_id(self):
        mgr = DownloadTaskManager(max_workers=2)
        try:
            tid = mgr.submit(lambda: 42, function_name="test_func")
            assert isinstance(tid, str)
            assert len(tid) > 0
        finally:
            mgr.shutdown()

    def test_get_task_not_found(self):
        mgr = DownloadTaskManager(max_workers=1)
        try:
            assert mgr.get_task("nonexistent") is None
        finally:
            mgr.shutdown()

    def test_task_completes(self):
        mgr = DownloadTaskManager(max_workers=2)
        try:
            def slow_task():
                time.sleep(0.1)
                return 42
            tid = mgr.submit(slow_task, function_name="test_func")
            time.sleep(0.5)
            task = mgr.get_task(tid)
            assert task is not None
            assert task["status"] == "completed"
        finally:
            mgr.shutdown()

    def test_task_fails(self):
        mgr = DownloadTaskManager(max_workers=1)
        try:
            def failing():
                raise ValueError("boom")
            tid = mgr.submit(failing, function_name="test_func")
            time.sleep(0.3)
            task = mgr.get_task(tid)
            assert task["status"] == "failed"
            assert "boom" in task["error"]
        finally:
            mgr.shutdown()

    def test_task_dict_has_fields(self):
        mgr = DownloadTaskManager(max_workers=1)
        try:
            tid = mgr.submit(lambda: None, function_name="test_func")
            task = mgr.get_task(tid)
            assert "task_id" in task
            assert "function_name" in task
            assert "status" in task
            assert "submitted_at" in task
            assert "completed_at" in task
            assert "progress" in task
            assert "result" in task
            assert "error" in task
        finally:
            mgr.shutdown()

    def test_submit_with_kwargs(self):
        mgr = DownloadTaskManager(max_workers=1)
        try:
            def func(stock_code, period):
                return f"{stock_code}_{period}"
            tid = mgr.submit(func, function_name="test",
                             stock_code="600000.SH", period="1d")
            time.sleep(0.3)
            task = mgr.get_task(tid)
            assert task["status"] == "completed"
        finally:
            mgr.shutdown()

    def test_prune_completed(self):
        mgr = DownloadTaskManager(max_workers=1, max_completed=3)
        try:
            for i in range(5):
                tid = mgr.submit(lambda x=i: x, function_name=f"func_{i}")
                time.sleep(0.05)
            time.sleep(0.3)
            all_tasks = mgr._tasks
            assert len(all_tasks) <= 3
        finally:
            mgr.shutdown()

    def test_fast_tasks_never_lose_results(self):
        mgr = DownloadTaskManager(max_workers=8, max_completed=2000)
        task_ids = [
            mgr.submit(lambda: 1, function_name="fast")
            for _ in range(1000)
        ]
        mgr._executor.shutdown(wait=True)

        tasks = [mgr.get_task(task_id) for task_id in task_ids]
        assert all(task["status"] == "completed" for task in tasks)
        assert all(task["result"] == 1 for task in tasks)

    def test_invalid_limits_are_rejected(self):
        with pytest.raises(ValueError):
            DownloadTaskManager(max_workers=0)
        with pytest.raises(ValueError):
            DownloadTaskManager(max_completed=0)
