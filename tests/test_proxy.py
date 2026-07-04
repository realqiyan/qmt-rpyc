import pytest
from client.proxy import (
    _RemoteCallable, _RemoteModule, _RemoteTrader, DownloadTaskHandle,
)


class FakeClient:
    def __init__(self):
        self.calls = []
    def _call(self, surface, name, args, kwargs):
        self.calls.append((surface, name, args, kwargs))
        return {"result": f"{surface}.{name}"}


class TestRemoteCallable:
    def test_call_forwards_to_client(self):
        client = FakeClient()
        callable_obj = _RemoteCallable(client, "xtdata", "get_market_data",
                                        {"signature": "()", "doc": "test"})
        result = callable_obj([], ["600000.SH"], "1d")
        assert result == {"result": "xtdata.get_market_data"}
        assert client.calls[-1] == ("xtdata", "get_market_data", ([], ["600000.SH"], "1d"), {})

    def test_preserves_doc_and_name(self):
        client = FakeClient()
        callable_obj = _RemoteCallable(client, "xtdata", "get_market_data",
                                        {"signature": "()", "doc": "获取行情数据"})
        assert callable_obj.__doc__ == "获取行情数据"
        assert callable_obj.__name__ == "get_market_data"


class TestRemoteModule:
    def test_builds_functions(self):
        client = FakeClient()
        desc = {"functions": {"get_market_data": {"signature": "()", "doc": "test"}}}
        mod = _RemoteModule(client, "xtdata", desc)
        assert hasattr(mod, "get_market_data")
        assert isinstance(mod.get_market_data, _RemoteCallable)

    def test_builds_constants(self):
        client = FakeClient()
        desc = {"constants": {"STOCK_BUY": 23, "STOCK_SELL": 24}}
        mod = _RemoteModule(client, "xtconstant", desc)
        assert mod.STOCK_BUY == 23
        assert mod.STOCK_SELL == 24

    def test_dir(self):
        client = FakeClient()
        desc = {"functions": {"get_market_data": {"signature": "()", "doc": "t"}},
                "constants": {"STOCK_BUY": 23}}
        mod = _RemoteModule(client, "xtconstant", desc)
        d = dir(mod)
        assert "get_market_data" in d
        assert "STOCK_BUY" in d


class TestRemoteTrader:
    def test_builds_methods(self):
        client = FakeClient()
        desc = {"methods": {"order_stock": {"signature": "()", "doc": "下单"},
                             "connect": {"signature": "()", "doc": "连接"}}}
        trader = _RemoteTrader(client, desc)
        assert hasattr(trader, "order_stock")
        assert hasattr(trader, "connect")
        assert isinstance(trader.order_stock, _RemoteCallable)


class TestDownloadTaskHandle:
    def test_task_id(self):
        client = FakeClient()
        handle = DownloadTaskHandle(client, "abc123")
        assert handle.task_id == "abc123"
