"""Server-only anti-corruption layer: adapter strategies selected per endpoint.

Register additional strategies before constructing ContractDispatcher. A strategy
can target the same wire version as another while declaring different SDK probes,
argument translations, constant mappings, and result transformations.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
import inspect
import logging

from qmt_rpyc.contract import (
    CONTRACT_VERSION, ContractFailure, bind, manifest, method_spec,
    project_result, specification,
)
from qmt_rpyc.server.serializer import serialize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SdkEnvironment:
    xtdata: object
    trader_type: object
    constants: object
    connection: object

    def trader_call(self, name, parameters):
        if self.connection is None:
            raise ContractFailure('NotConnected', 'trader.' + name, 'trader not configured')
        response = self.connection.call_trader_method(name, [], parameters)
        if response['status'] != 'ok':
            raise ContractFailure(
                response.get('error_type', 'SDKError'), 'trader.' + name,
                response.get('error_message', 'SDK call failed'),
                response.get('phase', 'sdk_execution'),
                response.get('outcome', 'unknown'))
        return response['data']


def signature_problem(fn, expected, unbound=False):
    """Compare structure and defaults, ignoring annotations and formatting."""
    if not callable(fn):
        return 'SDK dependency is missing or not callable'
    try:
        actual = inspect.signature(fn)
    except (TypeError, ValueError):
        return 'SDK signature cannot be inspected'
    params = list(actual.parameters.values())
    if unbound and params and params[0].name == 'self':
        params = params[1:]
    def shape(items):
        return [(p.name, p.kind, type(p.default), p.default) for p in items]
    if shape(params) != shape(expected.parameters.values()):
        return 'SDK signature mismatch: expected {}, found {}'.format(expected, actual)
    return None


class SdkAdapter(ABC):
    """Strategy boundary. probe must inspect definitions only, without SDK calls."""
    adapter_id = ''
    contract_version = CONTRACT_VERSION
    priority = 0

    @abstractmethod
    def supports(self, api):
        pass

    @abstractmethod
    def probe(self, api, environment):
        """Return None when compatible, otherwise a diagnostic reason."""

    @abstractmethod
    def invoke(self, api, parameters, environment):
        """Translate V1 input, invoke SDK, and translate raw output to V1."""


class AdapterRegistry:
    def __init__(self, adapters=()):
        self._adapters = []
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter):
        if not adapter.adapter_id or any(a.adapter_id == adapter.adapter_id for a in self._adapters):
            raise ValueError('adapter IDs must be nonempty and unique')
        self._adapters.append(adapter)

    def select(self, version, api, environment):
        matches, problems = [], []
        for adapter in self._adapters:
            if adapter.contract_version != version or not adapter.supports(api):
                continue
            try:
                problem = adapter.probe(api, environment)
            except Exception:
                logger.exception('Adapter %s probe failed for %s', adapter.adapter_id, api)
                problem = 'adapter compatibility probe failed'
            if problem is None:
                matches.append(adapter)
            else:
                problems.append('{}: {}'.format(adapter.adapter_id, problem))
        if not matches:
            return None, '; '.join(problems) or 'no adapter registered'
        matches.sort(key=lambda a: a.priority, reverse=True)
        if len(matches) > 1 and matches[0].priority == matches[1].priority:
            return None, 'ambiguous adapters at the same priority'
        return matches[0], None


class ContractDispatcher:
    def __init__(self, environment, registry):
        self.environment = environment
        self._selected = {}
        self._capabilities = {}
        for api in specification()['methods']:
            adapter, problem = registry.select(CONTRACT_VERSION, api, environment)
            self._selected[api] = adapter
            self._capabilities[api] = dict(available=adapter is not None,
                                          adapter_id=adapter.adapter_id if adapter else None,
                                          reason=problem)
            if problem:
                logger.warning('Contract V%s %s unavailable: %s', CONTRACT_VERSION, api, problem)

    def surface(self):
        return manifest(self._capabilities)

    def prepare(self, api, args, kwargs):
        parameters = bind(api, args, kwargs)
        if self._selected[api] is None:
            raise ContractFailure('APIUnavailable', api, self._capabilities[api]['reason'])
        return parameters

    def execute(self, api, parameters):
        try:
            value = self._selected[api].invoke(api, parameters, self.environment)
            return project_result(api, serialize(value), parameters)
        except ContractFailure as e:
            # The shared Trader connection reports whether SDK execution began.
            # An unsuccessful read has no uncertain trading side effect.
            if e.outcome == 'unknown' and not method_spec(api)['mutation']:
                raise ContractFailure(e.category, api, str(e), e.phase,
                                      'not_applicable') from e
            raise
        except Exception as e:
            logger.exception('Adapter execution failed for %s', api)
            raise ContractFailure('SDKError', api, '{}: SDK call failed'.format(type(e).__name__),
                                  'sdk_execution', 'unknown' if method_spec(api)['mutation'] else 'not_applicable') from e

    def call(self, api, args, kwargs):
        try:
            return {'status': 'ok', 'data': self.execute(api, self.prepare(api, args, kwargs))}
        except ContractFailure as e:
            return e.response()


def create_dispatcher(connection):
    from xtquant import xtdata, xtconstant
    from xtquant.xttrader import XtQuantTrader
    from qmt_rpyc.server.adapters.baseline import BaselineV1Adapter
    # Add production strategies here after validating them against their own SDK
    # evidence. Never relax baseline probes to silently accept unknown builds.
    registry = AdapterRegistry([BaselineV1Adapter()])
    return ContractDispatcher(SdkEnvironment(xtdata, XtQuantTrader, xtconstant, connection), registry)
