"""Typed calls over a real local RPyC socket with synthetic SDK data."""
import threading
from datetime import date
import pytest
from tests.fixtures import mock_xtquant, service
from qmt_rpyc import QmtClient
from qmt_rpyc.contracts.trading import Submitted
from qmt_rpyc.contracts.errors import NotConnectedError


@pytest.fixture
def mock_server(service):
    from rpyc.utils.server import ThreadedServer
    ready = threading.Event()
    class Server(ThreadedServer):
        def _listen(self):
            super()._listen()
            ready.set()
    server = Server(type(service), hostname='127.0.0.1', port=0,
                    protocol_config={'allow_public_attrs': False, 'allow_pickle': False})
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    assert ready.wait(3)
    yield server
    server.close()
    thread.join(3)


def test_typed_operations_over_socket(mock_server):
    with QmtClient.connect('127.0.0.1', port=mock_server.port) as client:
        assert len(client.capabilities().operations) == 28
        ticks = client.market.get_ticks(['600000.SH']).require_all()
        assert isinstance(ticks['600000.SH'].bid_prices, tuple)
        bars = client.market.get_daily_bars(['600000.SH'], count=2).require_all()
        assert bars['600000.SH'].rows[0].trade_date == date(2026, 9, 18)
        assert client.health().contract_version == 2
        result = client.trading.submit_order('ACC1', '600000.SH', 'BUY', 100, 10.0)
        assert isinstance(result, Submitted)
        assert result.order_id in {order.order_id for order in client.trading.list_orders('ACC1')}
        task = client.downloads.start_history('600000.SH', '1d')
        assert client.downloads.handle(task).wait(timeout=2).status == 'completed'
    with pytest.raises(NotConnectedError):
        client.health()
    client.close()


def test_authenticated_socket_rejects_wrong_key_and_serves_typed_health(service):
    from qmt_rpyc.transport.auth import make_server_authenticator
    from qmt_rpyc.server.auth_limiter import AuthRateLimiter
    from qmt_rpyc.contracts.errors import QmtAuthError
    from rpyc.utils.server import ThreadedServer
    cls = type(service)
    cls._require_auth = True
    server = ThreadedServer(cls, hostname='127.0.0.1', port=0,
                            authenticator=make_server_authenticator('synthetic-key', AuthRateLimiter()),
                            protocol_config={'allow_public_attrs': False, 'allow_pickle': False})
    port = server.listener.getsockname()[1]
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    try:
        with pytest.raises(QmtAuthError):
            QmtClient.connect('127.0.0.1', port, auth_key='incorrect')
        with QmtClient.connect('127.0.0.1', port, auth_key='synthetic-key') as client:
            assert client.system.get_health().connected
    finally:
        server.close()
        thread.join(3)
        cls._require_auth = False
