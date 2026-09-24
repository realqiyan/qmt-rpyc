"""Read-only diagnostic probes of the current public capabilities."""
import time

from qmt_rpyc.contracts.errors import QmtError


def run_self_test(client, test_symbols=None, timeout=30):
    symbols = {'sh_stock': '600000.SH', 'etf': '510050.SH', 'sector': '沪深A股', 'market': 'SH'}
    symbols.update(test_symbols or {})
    code, underlying = symbols['sh_stock'], symbols['etf']
    probes = {
        'reference.list_sectors': lambda: client.reference.list_sectors(),
        'reference.get_sector_members': lambda: client.reference.get_sector_members(symbols['sector']),
        'reference.get_dividend_events': lambda: client.reference.get_dividend_events(code),
        'reference.get_index_weights': lambda: client.reference.get_index_weights('000300.SH'),
        'instruments.list_option_underlyings': lambda: client.instruments.list_option_underlyings(),
        'instruments.get_details': lambda: client.instruments.get_details([code]).require_all(),
        'instruments.get_trading_reference': lambda: client.instruments.get_trading_reference([code]).require_all(),
        'options.get_expiry_dates': lambda: client.options.get_expiry_dates(underlying),
        'market.get_ticks': lambda: client.market.get_ticks([code]).require_all(),
        'market.get_market_ticks': lambda: client.market.get_market_ticks([symbols['market']]),
        'market.get_daily_bars': lambda: client.market.get_daily_bars([code], count=2).require_all(),
        'market.get_intraday_bars': lambda: client.market.get_intraday_bars([code], '1m', count=2).require_all(),
        'market.get_trading_dates': lambda: client.market.get_trading_dates(symbols['market'], count=2),
        'financials.get_reports': lambda: client.financials.get_reports([code]).require_all(),
        'system.get_health': lambda: client.system.get_health(),
        'system.get_capabilities': lambda: client.system.get_capabilities(),
    }
    account = symbols.get('account_id')
    for name in ('get_asset', 'list_positions', 'list_orders'):
        probes['trading.' + name] = (lambda method=name: getattr(client.trading, method)(account)) if account else None
    start, results = time.monotonic(), []
    capabilities = client.capabilities().operations
    for operation, probe in probes.items():
        status, message = 'passed', ''
        if probe is None or not capabilities[operation].available:
            status, message = 'skipped', 'required account or capability unavailable'
        elif time.monotonic() - start >= timeout:
            status, message = 'skipped', 'diagnostic time budget exhausted'
        else:
            try:
                probe()
            except QmtError as exc:
                status = 'skipped' if exc.error_type in ('API_UNAVAILABLE', 'NOT_CONNECTED') else 'failed'
                message = str(exc)
            except Exception as exc:
                status, message = 'failed', str(exc)
        results.append(dict(operation=operation, status=status, message=message))
    return dict(total=len(results), passed=sum(r['status']=='passed' for r in results),
                failed=sum(r['status']=='failed' for r in results), skipped=sum(r['status']=='skipped' for r in results),
                duration_seconds=time.monotonic()-start, results=results)
