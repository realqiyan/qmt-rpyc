"""Redaction helpers for values that must not reach logs or stdout verbatim."""


def mask_account(account_id):
    """Return a masked account id for logs and startup output.

    Keeps the first and last two characters so operators can still tell
    accounts apart, and never returns an empty string.
    """
    if not account_id or len(account_id) <= 4:
        return account_id or "(none)"
    return account_id[:2] + "*" * (len(account_id) - 4) + account_id[-2:]
