"""Contract negotiation and correlated request/response envelopes."""
import uuid

from qmt_rpyc.contracts.common import BatchResult, OperationError
from qmt_rpyc.contracts.errors import OutcomeUnknownError, ProtocolError, QmtError
from qmt_rpyc.contracts.operations import CONTRACT_HASH, CONTRACT_VERSION, OPERATIONS
from qmt_rpyc.contracts.system import Capabilities
from qmt_rpyc.contracts.validation import validate_result

from .codec import decode, dumps, encode, loads


def negotiate(connection):
    try:
        response = loads(connection.root.negotiate(CONTRACT_HASH))
    except Exception as exc:
        raise ProtocolError("contract negotiation failed") from exc
    if (type(response) is not dict
            or set(response) != {"contract_version", "contract_hash", "capabilities"}
            or type(response["contract_version"]) is not int
            or response["contract_version"] != CONTRACT_VERSION
            or response["contract_hash"] != CONTRACT_HASH):
        raise ProtocolError("contract negotiation mismatch")
    capabilities = decode(Capabilities, response["capabilities"])
    if set(capabilities.operations) != set(OPERATIONS):
        raise ProtocolError("negotiated capability operation set mismatch")
    return capabilities


def invoke(connection, operation, request):
    spec = OPERATIONS[operation]
    # Validate before dispatch: invalid arguments are definitely unexecuted.
    request = decode(spec.request_type, encode(request))
    if hasattr(request, "codes") and not request.codes and not spec.mutation:
        return BatchResult(())
    request_id = uuid.uuid4().hex
    payload = dumps({"contract_version": CONTRACT_VERSION, "request_id": request_id,
                     "operation": operation, "payload": encode(request)})
    try:
        raw = connection.root.call(payload)
    except Exception as exc:
        error = OperationError("TRANSPORT_ERROR", "transport failed", operation,
                                 CONTRACT_VERSION, "transport", "unknown" if spec.mutation else "not_applicable", request_id)
        raise (OutcomeUnknownError(error) if spec.mutation else QmtError(error)) from exc
    try:
        response = loads(raw)
        if type(response) is not dict:
            raise ProtocolError("response must be an object")
        if (type(response.get("contract_version")) is not int
                or response.get("contract_version") != CONTRACT_VERSION
                or response.get("request_id") != request_id
                or response.get("operation") != operation):
            raise ProtocolError("response context mismatch")
        common = {"contract_version", "request_id", "operation", "status"}
        if response.get("status") == "error":
            if set(response) != common | {"error"}:
                raise ProtocolError("invalid error envelope")
            error = decode(OperationError, response["error"])
            if (error.request_id != request_id or error.operation != operation
                    or error.contract_version != CONTRACT_VERSION):
                raise ProtocolError("error context mismatch")
            if spec.mutation and error.outcome == "not_applicable":
                raise ProtocolError("mutation error omitted execution outcome")
            if spec.mutation and error.outcome == "not_executed" and error.phase not in ("pre_execution", "dispatch"):
                raise ProtocolError("unsafe not_executed claim after execution")
            raise OutcomeUnknownError(error) if error.outcome == "unknown" else QmtError(error)
        if response.get("status") != "ok" or set(response) != common | {"data"}:
            raise ProtocolError("invalid success envelope")
        result = decode(spec.response_type, response["data"])
        validate_result(operation, request, result)
        return result
    except QmtError:
        raise
    except Exception as exc:
        if spec.mutation:
            raise OutcomeUnknownError(OperationError("INVALID_RESULT", "mutation response is invalid; reconcile before retrying",
                                      operation, CONTRACT_VERSION, "result_validation", "unknown", request_id)) from exc
        raise ProtocolError("invalid response for " + operation + ": " + str(exc)) from exc
