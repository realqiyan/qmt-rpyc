"""Adapter for the deployed 2026-09-23 SDK definitions, not a package version."""
from qmt_rpyc.contract import signature, specification
from qmt_rpyc.server.adapters import SdkAdapter, signature_problem
from qmt_rpyc.server.adapters.ticks import normalize_tick_times


class BaselineV1Adapter(SdkAdapter):
    adapter_id = 'xtquant-baseline-20260923-v1'

    def supports(self, api):
        return api in specification()['methods']

    def probe(self, api, environment):
        surface, name = api.split('.')
        owner = environment.xtdata if surface == 'xtdata' else environment.trader_type
        problem = signature_problem(getattr(owner, name, None), signature(api), surface == 'trader')
        if problem:
            return problem
        # Option names come from a second SDK call; that dependency is checked too.
        if name == 'get_option_detail_data':
            problem = signature_problem(getattr(environment.xtdata, 'get_instrument_detail', None),
                                        signature('xtdata.get_instrument_detail'))
            if problem:
                return 'get_instrument_detail: ' + problem
        constants = specification()['constants']
        required = []
        if name in ('order_stock', 'query_stock_orders'):
            required = [k for k in constants if k.startswith(('STOCK_', 'ORDER_')) or k == 'LATEST_PRICE']
        elif name == 'cancel_order_stock_sysid':
            required = ['SH_MARKET', 'SZ_MARKET']
        for key in required:
            value = getattr(environment.constants, key, None)
            if type(value) is not int or value != constants[key]:
                return 'SDK constant mismatch: ' + key
        return None

    def invoke(self, api, parameters, environment):
        surface, name = api.split('.')
        params = dict(parameters)
        if surface == 'trader':
            return environment.trader_call(name, params)
        # Always pass fixed defaults explicitly. Empty SDK lists are expanded to
        # prevent an SDK adding columns/tables from expanding the bridge contract.
        if name == 'get_market_data_ex' and not params['field_list']:
            params['field_list'] = specification()['bar_default_fields']
        if name in ('get_financial_data', 'download_financial_data') and not params['table_list']:
            params['table_list'] = specification()['financial_tables']
        result = getattr(environment.xtdata, name)(**params)
        if name == 'get_full_tick':
            return normalize_tick_times(result)
        if name.startswith('download_'):
            # V1 completion is defined by normal SDK return, not its payload.
            return None
        if name == 'get_option_detail_data' and result == {}:
            # The baseline returns {} for a known non-option instrument.
            return None
        if name == 'get_option_detail_data' and result:
            instrument = environment.xtdata.get_instrument_detail(params['optioncode'], iscomplete=True)
            result = dict(result)
            if instrument and 'InstrumentName' in instrument:
                result['InstrumentName'] = instrument['InstrumentName']
        return result
