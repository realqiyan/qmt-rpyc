#!/usr/bin/env bash
# ============================================================
# qmt-rpyc Client Test Suite (cross-platform)
#
# Runs tests that only depend on client/ and common/ packages.
# No numpy, pandas, or xtquant required — works on Linux,
# macOS, and Windows (Git Bash / WSL).
#
# Prerequisites:
#   - Python >= 3.9
#   - pip install -e .  (or pip install rpyc>=6.0.0)
#
# Usage:
#   bash scripts/test.sh              Run all client tests
#   bash scripts/test.sh -k "protocol"  Filter by keyword
#   bash scripts/test.sh -v -x          Verbose, stop on first failure
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CLIENT_TESTS=(
    "tests/test_protocol.py"
    "tests/test_client_exceptions.py"
    "tests/test_proxy.py"
    "tests/test_config.py"
    "tests/test_cli.py"
    "tests/test_auth_limiter.py"
    "tests/test_datetime_patch.py"
    "tests/test_event_bus.py"
)

echo "=== qmt-rpyc client test suite ==="
echo ""

cd "$PROJECT_DIR"

# Make sure project root is on PYTHONPATH so server/ modules
# used by auth_limiter / datetime_patch / event_bus tests are
# importable from a dev checkout.
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -n "${PYTHON_EXE:-}" ]; then
    TEST_PYTHON="$PYTHON_EXE"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    TEST_PYTHON="$PROJECT_DIR/.venv/bin/python"
else
    TEST_PYTHON="python"
fi

"$TEST_PYTHON" -m pytest "${CLIENT_TESTS[@]}" -v "$@"
