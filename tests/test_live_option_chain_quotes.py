"""Opt-in acceptance: every discovered SH/SZ chain contract needs a real quote."""
import os
from datetime import date

import pytest

from qmt_rpyc import QmtClient

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get('QMT_RPYC_LIVE') != '1', reason='deployment acceptance is opt-in')]


@pytest.mark.parametrize('underlying', ['510300.SH', '159915.SZ'])
def test_live_entire_option_chain_has_quotes(underlying):
    with QmtClient.connect_profile(os.environ.get('QMT_RPYC_PROFILE','default'), timeout=60) as client:
        expiries = client.options.get_expiry_dates(underlying).dates
        future = [expiry for expiry in expiries if expiry > date.today()]
        assert future, 'no future expiry discovered for acceptance underlying'
        chain = client.options.get_option_chain(underlying, min(future))
        assert chain.contract_codes, 'empty acceptance chain'
        codes = (underlying,) + chain.contract_codes
        details = client.options.get_contract_details(chain.contract_codes).require_all()
        quotes = client.market.get_ticks(codes).require_all()
        assert set(quotes) == set(codes)
        assert set(details) == set(chain.contract_codes)
        assert all(contract.expiry_date == min(future) for contract in details.values())
