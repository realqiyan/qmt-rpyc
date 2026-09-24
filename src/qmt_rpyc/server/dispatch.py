"""request boundary; only JSON primitives cross RPyC."""
import logging

from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.adapters.interfaces import Providers
from qmt_rpyc.contracts.errors import ProtocolError
from qmt_rpyc.contracts.system import Capabilities, Capability
from qmt_rpyc.contracts.validation import validate_result
from qmt_rpyc.transport import codec

from .download_service import DownloadService
from .system import SystemService

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1024 * 1024

class Dispatcher:
    """Backend injection is the sole SDK seam; public codec stays unchanged."""
    def __init__(self, providers: Providers, downloads=None, health=lambda: {}, active_clients=lambda: 0):
        from qmt_rpyc.contracts.operations import OPERATIONS
        capabilities = dict(providers.capabilities.operations)
        if downloads is None:
            for name in capabilities:
                if name.startswith('downloads.'):
                    capabilities[name] = Capability(False, None, 'download manager unavailable')
        self.capabilities = Capabilities(capabilities)
        self.providers = providers
        services = {group: getattr(providers, group) for group in
                    ('reference', 'instruments', 'options', 'market', 'financials', 'trading')}
        services['downloads'] = DownloadService(providers.downloads, downloads)
        services['system'] = SystemService(self.capabilities, health, downloads, active_clients)
        self._handlers = {operation: getattr(services[operation.split('.')[0]], operation.split('.')[1])
                          for operation in OPERATIONS}

    def negotiate(self, expected_hash):
        from qmt_rpyc.contracts.operations import CONTRACT_HASH
        if expected_hash != CONTRACT_HASH:
            return codec.dumps({'status': 'error', 'error_type': 'CONTRACT_MISMATCH',
                                'message': 'manifest hash mismatch'})
        return codec.dumps(dict(contract_version=2, contract_hash=CONTRACT_HASH,
                                capabilities=self.capabilities))

    def call(self, payload):
        from qmt_rpyc.contracts.operations import OPERATIONS
        request_id, operation, phase, mutation = '', '', 'pre_execution', False
        try:
            if type(payload) is not str or len(payload.encode('utf-8')) > MAX_REQUEST_BYTES:
                raise ProtocolError('invalid request payload size/type')
            envelope = codec.loads(payload)
            if not isinstance(envelope, dict) or set(envelope) != {'contract_version', 'request_id', 'operation', 'payload'}:
                raise ProtocolError('invalid request envelope')
            request_id, operation = envelope['request_id'], envelope['operation']
            if type(request_id) is not str or not request_id or len(request_id) > 128:
                raise ProtocolError('invalid request ID')
            if type(envelope['contract_version']) is not int or envelope['contract_version'] != 2:
                raise ProtocolError('invalid contract version')
            if type(operation) is not str or operation not in OPERATIONS:
                raise ProtocolError('unknown operation')
            descriptor = OPERATIONS[operation]
            mutation = descriptor.mutation
            request = codec.decode(descriptor.request_type, envelope['payload'])
            capability = self.capabilities.operations[operation]
            if not capability.available:
                raise ProviderError('API_UNAVAILABLE', operation, capability.reason)
            phase = 'sdk_execution'
            value = self._handlers[operation](request)
            phase = 'result_validation'
            result = codec.decode(descriptor.response_type, codec.encode(value))
            validate_result(operation, request, result)
            return codec.dumps(dict(contract_version=2, request_id=request_id,
                                    operation=operation, status='ok', data=result))
        except ProviderError as exc:
            return self.error(request_id, operation, exc.category,
                              str(exc), exc.phase, exc.outcome)
        except (ProtocolError, ValueError, TypeError, KeyError, OverflowError) as exc:
            logger.warning('%s failed during %s', operation, phase, exc_info=True)
            return self.error(request_id, operation,
                              'INVALID_ARGUMENTS' if phase == 'pre_execution' else 'INVALID_RESULT',
                              'request validation failed' if phase == 'pre_execution' else 'source result failed validation',
                              phase, 'not_executed' if phase == 'pre_execution' else ('unknown' if mutation else 'not_applicable'))
        except Exception:
            logger.exception('%s backend failure', operation)
            return self.error(request_id, operation, 'SOURCE_ERROR', 'backend operation failed', phase,
                              'not_executed' if phase == 'pre_execution' else ('unknown' if mutation else 'not_applicable'))

    @staticmethod
    def error(request_id, operation, category, message, phase, outcome):
        return codec.dumps(dict(contract_version=2, request_id=request_id, operation=operation, status='error',
                                error=dict(error_type=category, message=message, operation=operation,
                                           contract_version=2, phase=phase, outcome=outcome, request_id=request_id)))
