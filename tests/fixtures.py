import sys
import os
import time
import threading
import pytest


@pytest.fixture
def mock_xtquant():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from tests import _xtquant_mock
    sys.modules["xtquant"] = _xtquant_mock
    sys.modules["xtquant.xtdata"] = _xtquant_mock.xtdata
    sys.modules["xtquant.xttrader"] = _xtquant_mock
    sys.modules["xtquant.xttype"] = _xtquant_mock
    sys.modules["xtquant.xtconstant"] = _xtquant_mock.xtconstant
    yield
    for mod in list(sys.modules.keys()):
        if mod.startswith("xtquant") or mod.startswith("server."):
            del sys.modules[mod]


@pytest.fixture
def service(mock_xtquant):
    from qmt_rpyc.server.service import XtquantService
    from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
    from qmt_rpyc.server.downloads import DownloadTaskManager
    from qmt_rpyc.adapters.xtquant_2_0_6_1.discovery import build_api_surface
    from qmt_rpyc.adapters.xtquant_2_0_6_1.factory import create_providers
    from qmt_rpyc.server.dispatch import Dispatcher

    cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
    cm._init_trader()
    cm.connect()
    dm = DownloadTaskManager(max_workers=1)

    XtquantService._debug_handler = None
    XtquantService._auth_key = None
    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._dispatcher = Dispatcher(create_providers(cm), dm, cm.get_health_status, lambda: XtquantService._active_clients)

    svc = XtquantService()
    yield svc

    cm.stop()
    dm.shutdown()
