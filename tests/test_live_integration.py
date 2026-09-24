"""Opt-in read-only acceptance against a configured deployment."""
import os
import pytest
from qmt_rpyc import QmtClient

pytestmark = pytest.mark.skipif(os.environ.get('QMT_RPYC_LIVE') != '1', reason='set QMT_RPYC_LIVE=1 for deployment validation')


@pytest.fixture(scope='module')
def live_client():
    with QmtClient.connect_profile(os.environ.get('QMT_RPYC_PROFILE', 'default')) as client:
        yield client


def test_live_contract_and_health(live_client):
    assert len(live_client.capabilities().operations) == 28
    assert live_client.system.get_health().connected


def test_live_tick_and_instrument(live_client):
    code = os.environ.get('QMT_TEST_CODE', '510050.SH')
    assert live_client.market.get_ticks([code]).require_all()[code].observed_at.tzinfo
    assert live_client.instruments.get_details([code]).require_all()[code].name


def test_live_option_discovery_and_details(live_client):
    underlying = os.environ.get('QMT_TEST_UNDERLYING', '510050.SH')
    dates = live_client.options.get_expiry_dates(underlying)
    if not dates.dates:
        pytest.skip('no current option contracts')
    chain = live_client.options.get_option_chain(underlying, dates.dates[0])
    if not chain.contract_codes:
        pytest.skip('no current option chain')
    details = live_client.options.get_contract_details(chain.contract_codes[:10]).require_all()
    assert all(row.underlying == underlying for row in details.values())
