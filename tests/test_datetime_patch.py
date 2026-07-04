import sys
import datetime
import pytest


class TestDatetimePatch:
    def test_needs_patch_flag_exists(self):
        from server.datetime_patch import _NEEDS_PATCH, _PATCHED
        assert isinstance(_NEEDS_PATCH, bool)
        assert isinstance(_PATCHED, bool)

    def test_patched_when_needed(self):
        from server.datetime_patch import _NEEDS_PATCH, _PATCHED
        if _NEEDS_PATCH:
            assert _PATCHED is True
        else:
            assert _PATCHED is False

    def test_fromtimestamp_does_not_crash(self):
        result = datetime.datetime.fromtimestamp(1700000000.123456)
        assert isinstance(result, datetime.datetime)
