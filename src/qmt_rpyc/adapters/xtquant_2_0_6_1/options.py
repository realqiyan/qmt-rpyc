
from qmt_rpyc.adapters.errors import ItemFailure
from qmt_rpyc.contracts.common import BatchResult, CodesRequest
from qmt_rpyc.contracts.options import (
    ExpiryDates,
    ExpiryDatesRequest,
    OptionChain,
    OptionChainRequest,
    OptionContract,
)

from . import conversions as v
from .source import SdkSource


class OptionsAdapter:
    def __init__(self, source: SdkSource):
        self.b = source

    def get_expiry_dates(self, r: ExpiryDatesRequest) -> ExpiryDates:
        today = v.market_date()
        return ExpiryDates(today, tuple(sorted({expiry for _, expiry in self.b.candidates(r.underlying, today)})))

    def get_option_chain(self, r: OptionChainRequest) -> OptionChain:
        today = v.market_date()
        if r.expiry_date < today:
            from qmt_rpyc.adapters.errors import ProviderError
            raise ProviderError('INVALID_ARGUMENTS', 'options.get_option_chain', 'past expiry is outside current discovery')
        return OptionChain(today, tuple(sorted(
            code for code, expiry in self.b.candidates(r.underlying, today) if expiry == r.expiry_date)))

    def get_contract_details(self, r: CodesRequest) -> BatchResult[OptionContract]:
        def one(code):
            row = self.b.call('get_option_detail_data', code)
            if row is None:
                return None
            v.verify_identity(code, row)
            instrument = self.b.call('get_instrument_detail', code, True)
            if not instrument:
                raise ItemFailure('MISSING_RESULT', 'option name unavailable')
            v.verify_identity(code, instrument)
            return dict(underlying=v.underlying(row), name=instrument['InstrumentName'],
                        option_type=row['optType'], expiry_date=v.day(row['ExpireDate']),
                        strike_price=v.number(row['OptExercisePrice']),
                        contract_unit=v.integer(row['OptUnit'], positive=True))
        return self.b.batch(r.codes, OptionContract, one)
