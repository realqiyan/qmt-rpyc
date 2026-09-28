"""Python 3.6-compatible financial record encoding for the strategy bridge.

This module has no package imports so it can be bundled into the QMT strategy.
Integer time keys become explicit [time, value] pairs, never JSON object keys.
"""
import math


def _scalar(value):
    if type(value).__module__.startswith('numpy') and getattr(value, 'ndim', None) == 0:
        value = value.item()
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError('financial scalar must be numeric or null')
    if math.isnan(value):
        return None
    if not math.isfinite(value):
        raise ValueError('financial scalar must not be infinite')
    return value


def encode_raw_financial(source):
    """Encode scalar tables without losing field identities or time-key types.

NaN becomes an explicit null value; omitted fields/points stay omitted, so the
consumer can distinguish a supplied null from a missing record. This format
must not be used to flatten shareholder records with multiple rows per date.
"""
    if not isinstance(source, dict):
        raise ValueError('financial source must be a mapping')
    result = {}
    for code, fields in source.items():
        if type(code) is not str or not code or not isinstance(fields, dict):
            raise ValueError('invalid financial code/fields')
        encoded = {}
        for field, points in fields.items():
            if type(field) is not str or not field or not isinstance(points, dict):
                raise ValueError('invalid financial field/points')
            if field.split('.', 1)[0] in ('TOP10HOLDER', 'TOP10FLOWHOLDER'):
                raise ValueError('top-ten shareholder tables require a row-preserving source')
            pairs = []
            for key, value in points.items():
                if type(key).__module__.startswith('numpy') and getattr(key, 'ndim', None) == 0:
                    key = key.item()
                if type(key) is not int:
                    raise ValueError('financial time key must be an integer')
                pairs.append([key, _scalar(value)])
            encoded[field] = pairs
        result[code] = encoded
    return result
