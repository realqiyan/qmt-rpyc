import sys
import os
import math
from types import ModuleType
import pytest


@pytest.fixture(autouse=True)
def setup_mock():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from tests import _xtquant_mock
    _xtquant_mock.xttype = _xtquant_mock
    sys.modules["xtquant"] = _xtquant_mock
    sys.modules["xtquant.xtdata"] = _xtquant_mock.xtdata
    sys.modules["xtquant.xttrader"] = _xtquant_mock
    sys.modules["xtquant.xttype"] = _xtquant_mock
    sys.modules["xtquant.xtconstant"] = _xtquant_mock.xtconstant
    yield
    for mod in list(sys.modules.keys()):
        if mod.startswith("xtquant") or mod == "server.api_surface":
            del sys.modules[mod]


class TestBuildApiSurface:
    def test_external_callable_and_classes_are_not_exposed(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import _public_callables

        module = ModuleType("example_api")

        def local_function():
            return None

        local_function.__module__ = module.__name__
        module.local_function = local_function
        module.external_function = math.sin
        module.ImportedClass = dict

        assert _public_callables(module) == ["local_function"]

    def test_returns_dict_with_surfaces(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
        surface = build_api_surface()
        assert "xtdata" in surface
        assert "XtQuantTrader" in surface
        assert "xtconstant" in surface
        assert "xttype" in surface

    def test_xtdata_has_functions(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
        surface = build_api_surface()
        funcs = surface["xtdata"]["functions"]
        assert "get_market_data" in funcs
        assert "signature" in funcs["get_market_data"]
        assert "doc" in funcs["get_market_data"]

    def test_trader_has_methods(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
        surface = build_api_surface()
        methods = surface["XtQuantTrader"]["methods"]
        assert "order_stock" in methods
        assert "connect" in methods
        assert "query_stock_asset" in methods

    def test_dynamic_trader_method_keeps_existing_metadata_shape(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
        surface = build_api_surface()
        meta = surface["XtQuantTrader"]["methods"][
            "query_new_purchase_limit"]
        assert set(meta) == {"signature", "doc"}

    def test_xtconstant_has_values(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
        surface = build_api_surface()
        constants = surface["xtconstant"]["constants"]
        assert constants["STOCK_BUY"] == 23
        assert constants["STOCK_SELL"] == 24

    def test_xttype_has_classes(self):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
        surface = build_api_surface()
        assert "StockAccount" in surface["xttype"]["classes"]
