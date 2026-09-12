import pytest

from qmt_rpyc.server.redaction import mask_account


@pytest.mark.parametrize("account_id, expected", [
    ("123456789012", "12********12"),
    ("12345", "12*45"),
    ("1234", "1234"),
    ("123", "123"),
    ("", "(none)"),
    (None, "(none)"),
])
def test_mask_account_hides_the_middle(account_id, expected):
    assert mask_account(account_id) == expected
