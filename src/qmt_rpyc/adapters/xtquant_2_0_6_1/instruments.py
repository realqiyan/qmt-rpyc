from typing import Tuple

from qmt_rpyc.adapters.errors import ItemFailure
from qmt_rpyc.contracts.common import BatchResult, CodesRequest, EmptyRequest
from qmt_rpyc.contracts.instruments import Instrument, TradingReference

from . import conversions as v
from .source import SdkSource


class InstrumentsAdapter:
    def __init__(self, source: SdkSource):
        self.b = source

    def list_option_underlyings(self, r: EmptyRequest) -> Tuple[str, ...]:
        source = self.b.call('get_option_undl_data', None)
        today = v.market_date()
        return tuple(code for code in v.identities(source)
                     if self.b.candidates(code, today))

    def get_details(self, r: CodesRequest) -> BatchResult[Instrument]:
        def one(code):
            row = self.b.call('get_instrument_detail', code, True)
            if row is None:
                return None
            v.verify_identity(code, row)
            delivery = None
            extension = row.get('ExtendInfo') or {}
            owner_code, owner_market = extension.get('OptUndlCode'), extension.get('OptUndlMarket')
            if bool(owner_code) != bool(owner_market):
                raise ValueError('incomplete underlying metadata')
            if owner_code and owner_market:
                # This field exists only in the option source, never invent None
                # for an option when the supplemental source is unavailable.
                option = self.b.source_option(code)
                if option is None:
                    raise ItemFailure('MISSING_RESULT', 'option delivery metadata unavailable')
                v.verify_identity(code, option)
                delivery = v.day(option['EndDelivDate'])
            return dict(source_exchange=row['ExchangeID'], source_instrument_id=row['InstrumentID'],
                        name=row['InstrumentName'], created_date=v.source_date(row['CreateDate']),
                        listed_date=v.source_date(row['OpenDate']), expiry_date=v.source_date(row['ExpireDate']),
                        float_volume=v.number(row['FloatVolume']), total_volume=v.number(row['TotalVolume']),
                        source_volume_multiple=row['VolumeMultiple'], delivery_end_date=delivery)
        return self.b.batch(r.codes, Instrument, one)

    def get_trading_reference(self, r: CodesRequest) -> BatchResult[TradingReference]:
        def one(code):
            row = self.b.call('get_instrument_detail', code, False)
            if row is None:
                return None
            v.verify_identity(code, row)
            return dict(source_is_trading=row['IsTrading'], previous_close=v.number(row['PreClose']),
                        settlement_price=v.number(row['SettlementPrice']), upper_limit=v.number(row['UpStopPrice']),
                        lower_limit=v.number(row['DownStopPrice']), price_tick=v.number(row['PriceTick']))
        return self.b.batch(r.codes, TradingReference, one)
