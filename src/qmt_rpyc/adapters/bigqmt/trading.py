"""BigQMT STOCK trading with explicit identity reconciliation and no replay."""
import logging
import time
import uuid
from datetime import datetime
from dataclasses import asdict

from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.trading import Asset, Position, Order, Submitted, Rejected, RequestSucceeded, RequestRejected
from .conversions import number, SHANGHAI

logger = logging.getLogger(__name__)

STATUSES = {48: 'UNREPORTED', 49: 'WAIT_REPORTING', 50: 'REPORTED', 51: 'CANCEL_PENDING',
            52: 'PARTIAL_CANCEL_PENDING', 53: 'PARTIAL_CANCELLED', 54: 'CANCELLED',
            55: 'PARTIALLY_FILLED', 56: 'FILLED', 57: 'REJECTED'}


def unknown(message):
    return ProviderError('SOURCE_ERROR', '', message, 'sdk_execution', 'unknown')


class TradingAdapter:
    def __init__(self, transport, observation_seconds=10, clock=time.monotonic, sleep=time.sleep):
        self.transport, self.observation_seconds = transport, observation_seconds
        self.clock, self.sleep = clock, sleep

    def _call(self, command, args, timeout=None):
        result = self.transport.request(command, args, timeout=timeout)
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise ValueError('invalid trading envelope')
        if not result['ok']:
            outcome = result.get('outcome')
            if outcome not in ('unknown', 'not_executed'):
                raise unknown('invalid trading error outcome')
            raise ProviderError('SOURCE_ERROR', '', 'BigQMT trading command failed: ' + str(result.get('error')),
                                'sdk_execution' if outcome == 'unknown' else 'pre_execution', outcome)
        return result['data']

    def _read(self, account, kind, cancelable=False, timeout=None):
        value = self._call('trade_read', dict(account=account, kind=kind, cancelable_only=cancelable), timeout=timeout)
        for row in value['rows']:
            if row['m_strAccountID'] != account:
                raise ValueError('trade account mismatch')
        return value['account_type'], value['rows']

    def get_asset(self, r):
        kind, rows = self._read(r.account, 'ACCOUNT')
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError('ambiguous asset identity')
        row = rows[0]
        return Asset(r.account, number(row['m_dAvailable']), number(row['m_dFrozenCash']),
                     number(row['m_dStockValue']), number(row['m_dBalance']))

    def list_positions(self, r):
        kind, rows = self._read(r.account, 'POSITION')
        result = [Position(r.account, row['m_strInstrumentID'] + '.' + row['m_strExchangeID'],
                           row['m_nVolume'], row['m_nCanUseVolume'],
                           number(row['m_dOpenPrice']), number(row['m_dMarketValue'])) for row in rows]
        if len({p.instrument for p in result}) != len(result):
            raise ValueError('duplicate position identity')
        return tuple(sorted(result, key=lambda p: p.instrument))

    def list_orders(self, r):
        kind, rows = self._read(r.account, 'ORDER', r.cancelable_only)
        result = []
        for row in rows:
            result.append(Order(account=r.account,
                instrument=row['m_strInstrumentID'] + '.' + row['m_strExchangeID'],
                order_id=row['m_strOrderRef'], exchange_order_id=row['m_strOrderSysID'] or None,
                submitted_at=datetime.strptime(row['m_strInsertDate'] + row['m_strInsertTime'], '%Y%m%d%H%M%S').replace(tzinfo=SHANGHAI),
                side={23: 'BUY', 24: 'SELL'}.get(row['m_nOpType'], 'UNKNOWN'),
                pricing='UNKNOWN',
                submitted_price=number(row['m_dLimitPrice']), requested_quantity=row['m_nVolumeTotalOriginal'],
                filled_quantity=row['m_nVolumeTraded'], average_fill_price=number(row['m_dTradedPrice']),
                status=STATUSES.get(row['m_nOrderStatus'], 'UNKNOWN'), source_status=row['m_nOrderStatus'],
                source_status_message=row['m_strErrorMsg'], correlation_ref=row['m_strRemark']))
        if len({o.order_id for o in result}) != len(result):
            raise ValueError('duplicate order reference')
        return tuple(sorted(result, key=lambda o: (o.submitted_at, o.order_id)))

    def submit_order(self, r):
        marker = r.correlation_ref or 'qp' + uuid.uuid4().hex[:20]
        args = dict(account=r.account, instrument=r.instrument, side=r.side, quantity=r.quantity,
                    pricing=r.pricing, price=r.price if r.price is not None else 0,
                    strategy_name='', marker=marker)
        value = self._call('trade_submit', args)
        # Reconciliation runs outside QMT's callback: timers and heartbeats can progress.
        try:
            before = set(value['before_refs'])
            deadline = self.clock() + self.observation_seconds
            while self.clock() < deadline:
                _, rows = self._read(r.account, 'ORDER', timeout=max(.001, deadline - self.clock()))
                matches = [row for row in rows if row['m_strRemark'] == marker]
                for row in matches:
                    if (row['m_strOrderRef'] in before
                            or row['m_strInstrumentID'] + '.' + row['m_strExchangeID'] != r.instrument
                            or row['m_nVolumeTotalOriginal'] != r.quantity
                            or (row['m_nOpType'] in (23, 24) and row['m_nOpType'] != (23 if r.side == 'BUY' else 24))
                            or (r.pricing == 'LIMIT' and row['m_dLimitPrice'] != r.price)):
                        raise ValueError('submission correlation mismatch')
                broker = [row for row in matches if row['m_strOrderSysID']]
                if len(broker) > 1:
                    raise ValueError('ambiguous broker submission correlation')
                # The deployment produces a local signal row and a broker row
                # for one remark. Only the uniquely identified broker row can
                # establish an accepted order. Preserve source rows in queries.
                selected = broker[0] if broker else matches[0] if len(matches) == 1 and matches[0]['m_nOrderStatus'] == 57 else None
                if selected is not None:
                    ref = selected['m_strOrderRef']
                    if not isinstance(ref, str) or not ref:
                        raise ValueError('missing submission reference')
                    if selected['m_nOrderStatus'] == 57:
                        return Rejected(selected['m_strErrorMsg'] or 'broker rejected order')
                    return Submitted(ref)
                self.sleep(.2)
        except Exception as exc:
            raise unknown('submission reconciliation failed; inspect orders; do not retry') from exc
        raise unknown('submission not observed before deadline; inspect orders; do not retry')

    def cancel_order(self, r):
        value = self._call('trade_cancel', dict(account=r.account, target=asdict(r.target)))
        if type(value['accepted']) is not bool or type(value['source_code']) is not int:
            raise unknown('unrecognized cancellation response; do not retry')
        if value['accepted']:
            return RequestSucceeded()
        logger.warning('Native cancellation not accepted: source_code=%s', value['source_code'])
        return RequestRejected('cancellation request not accepted')
