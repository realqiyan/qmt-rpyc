"""Python 3.6 typed trading commands on the strategy owner thread."""
import math
import time
import logging

TRADE_FIELDS = {
    'ACCOUNT': ('m_strAccountID', 'm_nBrokerType', 'm_dAvailable', 'm_dFrozenCash', 'm_dStockValue', 'm_dBalance'),
    'POSITION': ('m_strAccountID', 'm_strInstrumentID', 'm_strExchangeID', 'm_nVolume',
                 'm_nCanUseVolume', 'm_nFrozenVolume', 'm_nOnRoadVolume', 'm_nYesterdayVolume',
                 'm_dOpenPrice', 'm_dMarketValue'),
    'ORDER': ('m_strAccountID', 'm_strInstrumentID', 'm_strExchangeID', 'm_strOrderRef',
              'm_strOrderSysID', 'm_strInsertDate', 'm_strInsertTime', 'm_nOpType',
              'm_nOrderPriceType', 'm_dLimitPrice', 'm_nVolumeTotalOriginal', 'm_nVolumeTraded',
              'm_dTradedPrice', 'm_nOrderStatus', 'm_strErrorMsg', 'm_strSource', 'm_strRemark'),
}


def trade_field(row, key):
    return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


class StrategyTrading:
    def __init__(self, context, api):
        self.context, self.api = context, api
        # Never evict write markers within an instance: capacity failure is explicit.
        self.attempts = set()

    def function(self, name):
        fn = self.api.get(name)
        if not callable(fn):
            raise NotImplementedError('required trading function unavailable')
        return fn

    def rows(self, account, kind):
        rows = self.function('get_trade_detail_data')(account, 'STOCK', kind)
        if not isinstance(rows, (list, tuple)) or len(rows) > 100000:
            raise ValueError('invalid trade query shape')
        if any(trade_field(row, 'm_strAccountID') != account for row in rows):
            raise ValueError('trade account mismatch')
        return rows

    def execute(self, command, args, deadline=None):
        dispatched = False
        try:
            account = args['account']
            if type(account) is not str or not account or len(account) > 128:
                raise ValueError('invalid account')
            accounts = self.rows(account, 'ACCOUNT')
            if not accounts and command == 'trade_read':
                return {'ok': True, 'data': {'account_type': None, 'rows': []}}
            if (len(accounts) != 1 or type(trade_field(accounts[0], 'm_nBrokerType')) is not int
                    or trade_field(accounts[0], 'm_nBrokerType') != 2):
                raise ValueError('exactly one STOCK account required')
            if command == 'trade_read':
                kind = args['kind']
                if kind not in TRADE_FIELDS or type(args['cancelable_only']) is not bool:
                    raise ValueError('invalid trade query')
                rows = accounts if kind == 'ACCOUNT' else self.rows(account, kind)
                if args['cancelable_only']:
                    if kind != 'ORDER':
                        raise ValueError('cancelability only applies to orders')
                    selected = []
                    for row in rows:
                        sysid = trade_field(row, 'm_strOrderSysID')
                        if not sysid:
                            continue
                        value = self.function('can_cancel_order')(sysid, account, 'STOCK')
                        if type(value) is not bool:
                            raise ValueError('invalid cancelability result')
                        if value:
                            selected.append(row)
                    rows = selected
                data = {'account_type': 2, 'rows': [{key: trade_field(row, key) for key in TRADE_FIELDS[kind]} for row in rows]}
            elif command == 'trade_submit':
                code, side, quantity = args['instrument'], args['side'], args['quantity']
                price, pricing, marker, strategy = args['price'], args['pricing'], args['marker'], args['strategy_name']
                if (type(code) is not str or len(code.split('.')) != 2 or code.split('.')[1] not in ('SH', 'SZ')
                        or len(code.split('.')[0]) != 6 or not code.split('.')[0].isdigit()
                        or side not in ('BUY', 'SELL') or type(quantity) is not int or quantity <= 0
                        or pricing not in ('LIMIT', 'LATEST_PRICE')
                        or type(price) not in (float, int) or not math.isfinite(price)
                        or price < 0 or (pricing == 'LIMIT' and price == 0)
                        or type(marker) is not str or not marker or len(marker.encode('ascii')) > 24
                        or type(strategy) is not str or len(strategy) > 128):
                    raise ValueError('invalid stock order arguments')
                submit = self.function('passorder')
                existing = self.rows(account, 'ORDER')
                key = (account, marker)
                if key in self.attempts or any(trade_field(row, 'm_strRemark') == marker for row in existing):
                    raise ValueError('correlation already used; reconcile previous attempt')
                if len(self.attempts) >= 10000:
                    raise ValueError('write marker capacity exhausted')
                before = [trade_field(row, 'm_strOrderRef') for row in existing]
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError('deadline expired before submission')
                self.attempts.add(key)
                dispatched = True
                raw = submit(23 if side == 'BUY' else 24, 1101, account, code,
                             11 if pricing == 'LIMIT' else 5, price, quantity, strategy, 2, marker, self.context)
                # Native return is diagnostic only; zero is NOT an order identity.
                if type(raw) is not int:
                    raise ValueError('unrecognized native submission result')
                data = {'native_return': raw, 'before_refs': before}
            elif command == 'trade_cancel':
                target = args['target']
                if not isinstance(target, dict):
                    raise ValueError('invalid cancellation target')
                rows = self.rows(account, 'ORDER')
                if target.get('kind') == 'order_id' and set(target) == {'kind', 'order_id'}:
                    matches = [r for r in rows if trade_field(r, 'm_strOrderRef') == target['order_id']]
                elif (target.get('kind') == 'exchange_order_id' and set(target) == {'kind', 'market', 'exchange_order_id'}
                      and target['market'] in ('SH', 'SZ')):
                    matches = [r for r in rows if trade_field(r, 'm_strOrderSysID') == target['exchange_order_id']
                               and trade_field(r, 'm_strExchangeID') == target['market']]
                else:
                    raise ValueError('invalid cancellation identity')
                if len(matches) != 1:
                    raise ValueError('cancellation requires exactly one matching order')
                row = matches[0]
                sysid = trade_field(row, 'm_strOrderSysID')
                if type(sysid) is not str or not sysid:
                    raise ValueError('order has no broker cancellation identity')
                checked = self.function('get_value_by_order_id')(sysid, account, 'STOCK', 'ORDER')
                keys = ('m_strAccountID', 'm_strInstrumentID', 'm_strExchangeID', 'm_strOrderRef', 'm_strOrderSysID')
                if any(trade_field(checked, key) != trade_field(row, key) for key in keys):
                    raise ValueError('broker identity lookup mismatch')
                permitted = self.function('can_cancel_order')(sysid, account, 'STOCK')
                if type(permitted) is not bool:
                    raise ValueError('invalid cancelability result')
                if not permitted:
                    return {'ok': True, 'data': {'accepted': False, 'source_code': 0}}
                cancel_fn = self.function('cancel')
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError('deadline expired before cancellation')
                dispatched = True
                raw = cancel_fn(sysid, account, 'STOCK', self.context)
                if type(raw) is not bool:
                    raise ValueError('unrecognized native cancellation result')
                data = {'accepted': raw, 'source_code': int(raw)}
            else:
                raise ValueError('unknown trade command')
            return {'ok': True, 'data': data}
        except Exception as exc:
            logging.getLogger(__name__).warning('Trading command %s failed (%s); dispatched=%s', command, type(exc).__name__, dispatched)
            return {'ok': False, 'error': type(exc).__name__,
                    'outcome': 'unknown' if dispatched else 'not_executed'}
