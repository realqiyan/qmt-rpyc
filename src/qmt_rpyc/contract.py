"""Versioned wire contract. No xtquant imports and no SDK discovery on clients.

The JSON snapshot is the source of truth for names, defaults, models and constants.
Versions are immutable once published; package and SDK versions are independent.
"""
import copy
import hashlib
import inspect
import json
import math
import re
from datetime import datetime
from pathlib import Path

from qmt_rpyc.protocol import API_SURFACE_SCHEMA_VERSION, PROTOCOL_VERSION
from qmt_rpyc.version import __version__

CONTRACT_VERSION = 1
SUPPORTED_CONTRACTS = (CONTRACT_VERSION,)
_SPEC = json.loads((Path(__file__).parent / 'contracts' / 'v1.json').read_text(encoding='utf-8'))
CONTRACT_HASH = hashlib.sha256(json.dumps(_SPEC, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class ContractFailure(Exception):
    """Internal structured failure; arguments and account data are never included."""
    def __init__(self, category, api, message, phase='pre_execution', outcome='not_executed'):
        super().__init__(message)
        self.category, self.api = category, api
        self.phase, self.outcome = phase, outcome

    def response(self):
        return dict(status='error', error_type=self.category, error_message=str(self),
                    api=self.api, contract_version=CONTRACT_VERSION,
                    phase=self.phase, outcome=self.outcome)


def specification():
    return copy.deepcopy(_SPEC)


def method_spec(api):
    try:
        return copy.deepcopy(_SPEC['methods'][api])
    except (KeyError, TypeError):
        raise ContractFailure('UnknownAPI', str(api), 'API is not part of contract V1') from None


def signature(api):
    return inspect.Signature([
        inspect.Parameter(p['name'], inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          default=copy.deepcopy(p.get('default', inspect.Parameter.empty)))
        for p in method_spec(api)['parameters']
    ])


def manifest(capabilities=None):
    result = dict(package_version=__version__, protocol_version=PROTOCOL_VERSION,
                  schema_version=API_SURFACE_SCHEMA_VERSION,
                  contract_version=CONTRACT_VERSION, contract_hash=CONTRACT_HASH,
                  supported_contracts=list(SUPPORTED_CONTRACTS),
                  xtdata={'functions': {}}, XtQuantTrader={'methods': {}},
                  xtconstant={'constants': copy.deepcopy(_SPEC['constants'])})
    for api, spec in _SPEC['methods'].items():
        surface, name = api.split('.')
        group, key = ('xtdata', 'functions') if surface == 'xtdata' else ('XtQuantTrader', 'methods')
        result[group][key][name] = {'signature': str(signature(api)), 'doc': 'Bridge contract V1: ' + api}
    result['capabilities'] = copy.deepcopy(capabilities or {})
    return result


def _bad(path, expected):
    raise ValueError('{}: expected {}'.format(path, expected))


def project(value, schema, path='result', params=None):
    """Validate and project known fields; never invent missing required values."""
    params = params or {}
    if isinstance(schema, str):
        if schema in _SPEC['models'] and schema != 'financial':
            return project(value, _SPEC['models'][schema], path, params)
        if schema in ('string', 'nonempty_string', 'date', 'date_code', 'timetag'):
            valid = isinstance(value, str)
            if schema == 'date_code':
                valid = ((isinstance(value, str) and bool(re.fullmatch(r'0|[0-9]{8}', value)))
                         or (type(value) is int and (value == 0 or 10000000 <= value <= 99999999)))
            elif schema == 'date':
                valid = valid and bool(re.fullmatch(r'[0-9]{8}', value))
                if valid:
                    try:
                        datetime.strptime(value, '%Y%m%d')
                    except ValueError:
                        valid = False
            elif schema == 'timetag':
                valid = valid and bool(re.fullmatch(r'[0-9]{8} [0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{3})?', value))
            elif schema == 'nonempty_string':
                valid = valid and bool(value.strip())
        elif schema in ('number', 'integer', 'float', 'bar_time'):
            valid = type(value) is int or (type(value) is float and math.isfinite(value))
            if schema in ('integer', 'bar_time'):
                valid = type(value) is int
            elif schema == 'float':
                valid = type(value) is float and math.isfinite(value)
            if valid and schema == 'bar_time':
                valid = len(str(value)) == (8 if params.get('period') == '1d' else 14)
                if valid:
                    try:
                        datetime.strptime(str(value), '%Y%m%d' if params.get('period') == '1d' else '%Y%m%d%H%M%S')
                    except ValueError:
                        valid = False
        elif schema == 'boolean':
            valid = type(value) is bool
        elif schema == 'null':
            valid = value is None
        else:
            raise ValueError('unknown schema ' + schema)
        if not valid:
            _bad(path, schema)
        return value
    kind = schema['type']
    if kind == 'scalar':
        result = project(value, schema['scalar'], path, params)
        if not schema['minimum'] <= result <= schema['maximum']:
            _bad(path, schema.get('unit', 'contracted range'))
        return result
    if kind == 'nullable':
        return None if value is None else project(value, schema['value'], path, params)
    if kind == 'enum':
        if not any(type(value) is type(v) and value == v for v in schema['values']):
            _bad(path, 'one of {}'.format(schema['values']))
        return value
    if kind == 'array':
        if not isinstance(value, list):
            _bad(path, 'list')
        return [project(v, schema['items'], path + '[]', params) for v in value]
    if not isinstance(value, dict):
        _bad(path, 'object')
    if kind == 'object':
        result = {}
        for key, child in schema['fields'].items():
            if key not in value:
                if key in schema.get('optional', []):
                    continue
                _bad(path + '.' + key, 'required field')
            result[key] = project(value[key], child, path + '.' + key, params)
        return result
    if kind == 'map':
        if any(not isinstance(k, str) or not k for k in value):
            _bad(path, 'nonempty string keys')
        return {k: project(v, schema['values'], path + '.*', params) for k, v in value.items()}
    if kind == 'table':
        columns, index, rows = (value.get(k) for k in ('columns', 'index', 'data'))
        if not all(isinstance(x, list) for x in (columns, index, rows)):
            _bad(path, 'split table')
        if any(not isinstance(c, str) for c in columns) or len(set(columns)) != len(columns):
            _bad(path + '.columns', 'unique column names')
        if len(index) != len(rows) or any(not isinstance(r, list) or len(r) != len(columns) for r in rows):
            _bad(path, 'matching table dimensions')
        # A no-data financial table can have no columns; do not fabricate them.
        if not columns and not rows:
            return dict(columns=[], index=[], data=[])
        wanted = list(schema['columns'])
        if any(c not in columns for c in wanted):
            _bad(path + '.columns', 'all contracted columns')
        offsets = [columns.index(c) for c in wanted]
        return dict(columns=wanted,
                    index=[project(i, schema['index'], path + '.index[]', params) for i in index],
                    data=[[project(row[i], schema['columns'][c], path + '.' + c, params)
                           for c, i in zip(wanted, offsets)] for row in rows])
    raise ValueError('unknown schema kind ' + kind)


def bind(api, args, kwargs):
    spec = method_spec(api)
    try:
        if not isinstance(args, (list, tuple)) or not isinstance(kwargs, dict):
            raise ValueError('args must be a sequence and kwargs an object')
        bound = signature(api).bind(*args, **kwargs)
        bound.apply_defaults()
        values = {p['name']: project(bound.arguments[p['name']], p['schema'], p['name'])
                  for p in spec['parameters']}
        for name, allowed in [('field_list', list(_SPEC['models']['bars']['columns'])),
                              ('table_list', _SPEC['financial_tables'])]:
            if name in values:
                items = values[name]
                if len(set(items)) != len(items) or any(item not in allowed for item in items):
                    raise ValueError(name + ' contains duplicate or unsupported values')
        for name in ('order_volume', 'order_id'):
            if name in values and values[name] <= 0:
                raise ValueError(name + ' must be positive')
        if 'count' in values and values['count'] < -1:
            raise ValueError('count must be -1 or nonnegative')
        return values
    except (TypeError, ValueError) as e:
        raise ContractFailure('InvalidArguments', api, str(e)) from e


def project_result(api, value, params):
    schema = method_spec(api)['returns']
    if schema == 'market':
        cols = params['field_list'] or _SPEC['bar_default_fields']
        model = copy.deepcopy(_SPEC['models']['bars'])
        model['columns'] = {c: model['columns'][c] for c in cols}
        schema = {'type': 'map', 'values': model}
    elif schema == 'instrument_choice':
        schema = 'instrument_complete' if params['iscomplete'] else 'instrument'
    elif schema == 'underlying_choice':
        schema = {'type': 'array', 'items': 'string'}
        if params['undl_code_ref'] is None:
            schema = {'type': 'map', 'values': schema}
    elif schema == 'financial_choice':
        tables = params['table_list'] or _SPEC['financial_tables']
        schema = {'type': 'map', 'values': {'type': 'object', 'fields': {
            t: _SPEC['models']['financial'][t] for t in tables}}}
    try:
        result = project(value, schema, params=params)
        if api == 'trader.order_stock' and result != -1 and result <= 0:
            raise ValueError('expected positive order ID or rejection -1')
        return result
    except ValueError as e:
        raise ContractFailure('InvalidResult', api, str(e), 'result_validation',
                              'unknown' if method_spec(api)['mutation'] else 'not_applicable') from e
