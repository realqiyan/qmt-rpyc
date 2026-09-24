#!/usr/bin/env bash
# ============================================================
#  qmt-rpyc — Client Environment Setup (Linux / macOS / WSL)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "============================================================"
echo " qmt-rpyc — Client Environment Setup"
echo "============================================================"
echo ""

cd "$PROJECT_ROOT"

# --- 1. check Python version --------------------------------------------------
echo "[1/3] Checking Python version..."

PYTHON_EXE="${PYTHON_EXE:-python3}"
if [ "$PYTHON_EXE" = python3 ] && ! command -v "$PYTHON_EXE" &>/dev/null; then
    echo "       python3 not found, trying python..."
    PYTHON_EXE="python"
fi

if ! command -v "$PYTHON_EXE" &>/dev/null; then
    echo "[ERROR] Python not found. Install Python 3.9+."
    echo "        https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VER=$("$PYTHON_EXE" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "        Found Python $PYTHON_VER  ($PYTHON_EXE)"

MAJOR=$(echo "$PYTHON_VER" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VER" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
    echo "[ERROR] Python 3.9+ required, got $PYTHON_VER"
    exit 1
fi

# --- 2. create venv (optional) ------------------------------------------------
echo ""
echo "[2/3] Setting up virtual environment..."

VENV_DIR="${VENV_DIR:-.venv}"
if [ -f "$VENV_DIR/bin/python" ]; then
    echo "        $VENV_DIR already exists, skipping."
else
    "$PYTHON_EXE" -m venv "$VENV_DIR"
    echo "        Created $VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
"$VENV_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Existing venv requires Python 3.9+; recreate it.")'

# --- 3. install client dependencies -------------------------------------------
echo ""
echo "[3/3] Installing client dependencies..."

"$VENV_PYTHON" -m pip install --upgrade pip -q
"$VENV_PYTHON" -m pip install -e "$PROJECT_ROOT" -q
echo "        Done."

# --- done ---------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Setup complete."
echo ""
echo " Configure a profile with the server address and shared authentication key:"
echo "   $VENV_DIR/bin/qmt-rpyc-client init --profile office"
echo "   $VENV_DIR/bin/qmt-rpyc-client check --profile office"
echo ""
echo " Activate venv:  source $VENV_DIR/bin/activate"
echo "============================================================"
