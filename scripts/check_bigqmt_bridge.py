#!/usr/bin/env python3
"""Run bounded read-only adapter smoke checks on the Windows QMT host."""
import argparse
import json
from types import SimpleNamespace

from qmt_rpyc.adapters.bigqmt.factory import create_providers
from qmt_rpyc.adapters.bigqmt.reader import StrategyReader
from qmt_rpyc.adapters.bigqmt.transport import PipeTransport
from qmt_rpyc.contracts.common import CodesRequest, EmptyRequest
from qmt_rpyc.contracts.market import DailyBarsQuery, MarketTicksRequest, TradingDatesRequest
from qmt_rpyc.contracts.reference import IndexWeightsRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipe', default='qmt_rpyc_bridge_v1')
    parser.add_argument('--extended', action='store_true', help='also read whole-market ticks, sector tree and full index weights')
    args = parser.parse_args()
    transport = PipeTransport(args.pipe)
    transport.request('ping', {})
    providers = create_providers(SimpleNamespace(transport=transport))
    checks = []
    def run(name, call):
        try:
            value = call()
            if hasattr(value, 'require_all'):
                value = value.require_all()
            if hasattr(value, 'items') and not isinstance(value, dict):
                items = value.items
                if any(item.status != 'ok' for item in items):
                    raise ValueError('market contains item failures')
                value = items
            checks.append(dict(operation=name, status='returned', count=len(value) if hasattr(value, '__len__') else None))
        except Exception as exc:
            checks.append(dict(operation=name, status='failed', error_type=type(exc).__name__,
                               category=getattr(exc, 'category', None)))
        print(json.dumps(checks[-1], sort_keys=True), flush=True)

    run('market.get_ticks', lambda: providers.market.get_ticks(CodesRequest(('000001.SZ',))))
    run('market.get_daily_bars', lambda: providers.market.get_daily_bars(DailyBarsQuery(('000001.SZ',), count=2)))
    run('instruments.get_details', lambda: providers.instruments.get_details(CodesRequest(('000001.SZ',))))
    run('market.get_trading_dates', lambda: providers.market.get_trading_dates(TradingDatesRequest('SH', count=2)))
    def option():
        codes = StrategyReader(transport).get_option_codes('510050.SH')
        if not isinstance(codes, list) or not codes:
            raise ValueError('no option sample')
        return providers.options.get_contract_details(CodesRequest((codes[0],)))
    run('options.get_contract_details', option)
    if args.extended:
        run('market.get_market_ticks', lambda: providers.market.get_market_ticks(MarketTicksRequest(('SH', 'SZ'))))
        run('reference.list_sectors', lambda: providers.reference.list_sectors(EmptyRequest()))
        run('reference.get_index_weights', lambda: providers.reference.get_index_weights(IndexWeightsRequest('000300.SH')))
    print(json.dumps(dict(status='passed' if all(x['status'] == 'returned' for x in checks) else 'failed',
                          production_ready=False, trading_calls=0, financial_calls=0), sort_keys=True))
    return 0 if all(x['status'] == 'returned' for x in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
