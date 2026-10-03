"""Bounded provider reads with typed per-item results and no retries."""
from concurrent.futures import ThreadPoolExecutor

from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.contracts.common import BatchResult, Failure, ItemError, Success
from qmt_rpyc.transport.codec import decode, encode


def read_batch(codes, model, getter, workers, *, logger, missing_message):
    if type(workers) is not int or workers < 1:
        raise ValueError('workers must be positive')

    def one(code):
        try:
            value = getter(code)
            if value is None:
                raise ItemFailure('NOT_FOUND', missing_message)
            return Success(code, decode(model, encode(value)))
        except ProviderError as exc:
            if exc.category in ('NOT_CONNECTED', 'API_UNAVAILABLE'):
                raise
            logger.warning('Source item failed: code=%s', code, exc_info=True)
            return Failure(code, ItemError('SOURCE_ERROR', 'source item failed'))
        except (TypeError, ValueError, KeyError, OverflowError) as exc:
            logger.warning('Source item failed validation: code=%s model=%s',
                           code, model.__name__, exc_info=True)
            return Failure(code, ItemError(
                exc.code if isinstance(exc, ItemFailure) else 'INVALID_RESULT',
                str(exc) if isinstance(exc, ItemFailure) else 'source item does not satisfy the contract'))

    if not codes:
        return BatchResult(())
    with ThreadPoolExecutor(max_workers=min(workers, len(codes))) as pool:
        return BatchResult(tuple(pool.map(one, codes)))
