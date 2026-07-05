"""
Environment self-check for qmt-rpyc server.

Verifies Python version, core dependencies, .env config, MiniQMT running status,
and xtquant availability.  Auto-wires xtquant from the running QMT installation into the venv when
possible.  Falls back to QMT_PATH from .env when MiniQMT is not running.

Called by scripts/setup.bat after venv creation and dependency install.
Also usable standalone for diagnostics.

Critical failures exit non-zero.  MiniQMT not running is a warning, not fatal.
"""

import sys
import os
import ctypes
from ctypes import wintypes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _check(label: str, ok: bool, detail: str = "", fatal: bool = True) -> bool:
    """Print a check result. Returns True if passed (or non-fatal failure)."""
    status = "OK" if ok else ("WARN" if not fatal else "FAIL")
    line = f"  [{status}] {label}"
    if detail and not ok:
        line += f"  — {detail}"
    print(line)
    if not ok and fatal:
        return False
    return True


# ---------------------------------------------------------------------------
# MiniQMT process detection (Windows)
# ---------------------------------------------------------------------------

def _get_miniqmt_process():
    """Find the XtMiniQmt.exe process via psutil.

    Returns dict with keys 'exe', 'pid', 'install_dir' or None if not running.
    """
    try:
        import psutil
    except ImportError:
        return None

    for proc in psutil.process_iter(['name', 'exe', 'pid']):
        try:
            if proc.info['name'] == 'XtMiniQmt.exe':
                exe_path = proc.info['exe']
                return {
                    'exe': exe_path,
                    'pid': proc.info['pid'],
                    'install_dir': os.path.dirname(exe_path) if exe_path else '',
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return None


def _get_window_title(pid):
    """Enumerate top-level windows belonging to *pid* and return the best title.

    Prefers the visible window (the main QMT window with account info) over
    hidden helper windows like 'XtMiniQmt'.
    """
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM)

    visible_titles = []
    hidden_titles = []

    def _callback(hwnd, _lparam):
        pid_ptr = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ptr))
        if pid_ptr.value == pid:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title:
                    if user32.IsWindowVisible(hwnd):
                        visible_titles.append(title)
                    else:
                        hidden_titles.append(title)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(_callback), 0)

    # prefer visible window (main QMT), fall back to any titled window
    if visible_titles:
        return visible_titles[0]
    if hidden_titles:
        return hidden_titles[0]
    return None


def _extract_account(title):
    """Extract account ID from a QMT window title like '55011888 - xxx QMT ...'."""
    if title and ' - ' in title:
        part = title.split(' - ')[0].strip()
        if part.isdigit():
            return part
    return None


# ---------------------------------------------------------------------------
# xtquant discovery inside QMT installation
# ---------------------------------------------------------------------------

def _find_xtquant_in_dir(qmt_dir):
    """Search for xtquant under a QMT binary directory.

    Returns the site-packages path that contains xtquant, or None.
    """
    candidates = [
        os.path.join(qmt_dir, 'Lib', 'site-packages'),
        qmt_dir,
    ]
    for cand in candidates:
        if os.path.isdir(os.path.join(cand, 'xtquant')):
            return cand

    # shallow fallback walk — xtquant should only be 1-2 levels deep
    try:
        for root, dirs, _files in os.walk(qmt_dir):
            depth = root[len(qmt_dir):].count(os.sep)
            if depth > 3:
                dirs.clear()
                continue
            if 'xtquant' in dirs:
                return root
    except OSError:
        pass
    return None


def _configure_xtquant_path(xtquant_site):
    """Create a directory junction in the venv so Python finds xtquant.

    Uses mklink /J (directory junction, no admin rights needed) to expose
    ONLY the xtquant package — unlike the old .pth approach which added the
    entire QMT site-packages (including incompatible packages) to sys.path.
    """
    import subprocess

    site_packages = os.path.join(sys.prefix, 'Lib', 'site-packages')
    os.makedirs(site_packages, exist_ok=True)

    # source: QMT's xtquant directory
    qmt_xtquant = os.path.join(xtquant_site, 'xtquant')
    # target: venv's site-packages/xtquant
    venv_xtquant = os.path.join(site_packages, 'xtquant')

    # ── remove legacy .pth file ──────────────────────────────────────
    old_pth = os.path.join(site_packages, 'qmt_xtquant.pth')
    if os.path.isfile(old_pth):
        try:
            os.remove(old_pth)
            print(f"         Removed legacy .pth: {old_pth}")
        except OSError:
            pass

    # ── remove existing junction / directory if present ──────────────
    if os.path.isdir(venv_xtquant):
        try:
            subprocess.run(
                ['cmd.exe', '/c', 'rmdir', venv_xtquant],
                capture_output=True, shell=False,
            )
        except Exception:
            pass

    # ── create junction ──────────────────────────────────────────────
    try:
        subprocess.run(
            ['cmd.exe', '/c', 'mklink', '/J', venv_xtquant, qmt_xtquant],
            capture_output=True, shell=False, check=True,
        )
        print(f"         Created junction: {venv_xtquant} -> {qmt_xtquant}")
    except subprocess.CalledProcessError as e:
        print(f"         [WARN] Could not create junction: {e}")
        # fallback: still add to sys.path immediately
        if xtquant_site not in sys.path:
            sys.path.insert(0, xtquant_site)
        return

    # also make it available right now (the junction dir)
    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)


# ---------------------------------------------------------------------------
# .env auto-fill
# ---------------------------------------------------------------------------

_PLACEHOLDER_VALUES = {
    r"C:\path\to\MiniQMT\userdata_mini",
    "your-account-id",
}

def _update_env_file(qmt_path=None, account=None):
    """Patch .env with detected values for any fields that are still placeholders.

    Creates .env from .env.example if .env doesn't exist yet.
    """
    env_path = ".env"
    example_path = ".env.example"

    # auto-create .env from example if missing
    if not os.path.exists(env_path):
        if os.path.exists(example_path):
            import shutil
            shutil.copyfile(example_path, env_path)
            print(f"         Created .env from .env.example")
        else:
            return

    with open(env_path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n\r")

        # QMT_PATH=...
        if qmt_path and stripped.startswith("QMT_PATH="):
            _, val = stripped.split("=", 1)
            val = val.strip()
            if not val or val in _PLACEHOLDER_VALUES:
                lines[i] = f"QMT_PATH={qmt_path}\n"
                updated = True

        # QMT_ACCOUNT_ID=...
        if account and stripped.startswith("QMT_ACCOUNT_ID="):
            _, val = stripped.split("=", 1)
            val = val.strip()
            if not val or val in _PLACEHOLDER_VALUES:
                lines[i] = f"QMT_ACCOUNT_ID={account}\n"
                updated = True

    if updated:
        with open(env_path, "w", encoding="utf-8-sig") as f:
            f.writelines(lines)
        parts = []
        if qmt_path:
            parts.append(f"QMT_PATH={qmt_path}")
        if account:
            parts.append(f"QMT_ACCOUNT_ID={account}")
        print(f"         Auto-filled .env: {', '.join(parts)}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=== qmt-rpyc environment self-check ===\n")

    failures = 0

    # ---- 1. Python version -------------------------------------------------
    vi = sys.version_info
    ver_ok = (vi.major == 3 and vi.minor in (10, 11))
    if not _check(
        f"Python version ({vi.major}.{vi.minor}.{vi.micro})",
        ver_ok,
        "xtquant supports Python 3.10–3.11 only.",
    ):
        failures += 1

    # ---- 2. Core dependencies ----------------------------------------------
    print()
    print("Core dependencies:")

    deps = [
        ("rpyc", "rpyc>=6.0.0"),
        ("numpy", "numpy>=1.24,<2"),
        ("pandas", "pandas>=2.0,<3"),
        ("dotenv", "python-dotenv>=1.0.0"),
        ("psutil", "psutil"),
    ]

    for modname, spec in deps:
        try:
            __import__(modname if modname != "dotenv" else "dotenv")
            _check(f"  {modname}", True)
        except ImportError:
            _check(f"  {modname}", False, f"run: pip install {spec}")
            failures += 1

    # ---- 3. MiniQMT process -------------------------------------------------
    print()
    print("MiniQMT status:")

    miniqmt = _get_miniqmt_process()

    if miniqmt is None:
        _check("XtMiniQmt.exe", False,
               "MiniQMT is not running. Start MiniQMT and log in, then re-run.",
               fatal=False)
        miniqmt_running = False
    else:
        _check("XtMiniQmt.exe", True)
        print(f"         Install dir: {miniqmt['install_dir']}")
        title = _get_window_title(miniqmt['pid'])
        if title:
            print(f"         Window title: {title}")
            account = _extract_account(title)
            if account:
                print(f"         Account: {account}")
        miniqmt_running = True

    # ---- 4. xtquant --------------------------------------------------------
    print()
    print("xtquant module:")

    xtquant_ok = False

    if miniqmt_running:
        # auto-wire from running MiniQMT process
        xtquant_site = _find_xtquant_in_dir(miniqmt['install_dir'])
        if xtquant_site:
            _configure_xtquant_path(xtquant_site)
        else:
            # also try one level up (bin.x64 → QMT root)
            parent = os.path.dirname(miniqmt['install_dir'].rstrip('\\/'))
            if parent and os.path.isdir(parent):
                xtquant_site = _find_xtquant_in_dir(parent)
                if xtquant_site:
                    _configure_xtquant_path(xtquant_site)
            if not xtquant_site:
                print("         [WARN] xtquant not found inside MiniQMT install directory.")
    else:
        # offline fallback: try QMT_PATH from .env
        try:
            from dotenv import dotenv_values
            cfg = dotenv_values(".env")
            qmt_path = cfg.get("QMT_PATH", "")
        except Exception:
            qmt_path = ""

        if qmt_path and qmt_path not in _PLACEHOLDER_VALUES:
            qmt_site = os.path.abspath(
                os.path.join(qmt_path, "..", "bin.x64", "Lib", "site-packages")
            )
            if os.path.isdir(os.path.join(qmt_site, "xtquant")):
                _configure_xtquant_path(qmt_site)
            else:
                print(f"         [WARN] xtquant not found at computed path: {qmt_site}")
                print(f"                (QMT_PATH={qmt_path})")
        else:
            print("         MiniQMT not running and no valid QMT_PATH in .env.")
            print("         Start MiniQMT or set QMT_PATH in .env, then re-run.")

    try:
        import xtquant  # noqa: F401
        _check("xtquant importable", True)
        xtquant_ok = True
    except ImportError:
        _check("xtquant importable", False,
               "xtquant not on Python path. Start MiniQMT or set QMT_PATH in .env, "
               "then re-run this script.",
               fatal=False)

    # ---- 5. Auto-extract QMT_PATH & account from running MiniQMT -----------
    detected_qmt_path = None
    detected_account = None

    if miniqmt_running:
        # Derive QMT_PATH (userdata) from MiniQMT install dir
        #   install_dir  = D:\ACT\bin.x64  (where XtMiniQmt.exe lives)
        #   QMT_PATH     = D:\ACT\userdata_mini
        parent = os.path.dirname(miniqmt['install_dir'].rstrip('\\/'))
        if parent:
            candidate = os.path.join(parent, 'userdata_mini')
            if os.path.isdir(candidate):
                detected_qmt_path = candidate

        # Extract account from window title
        title = _get_window_title(miniqmt['pid'])
        if title:
            detected_account = _extract_account(title)

        if detected_qmt_path or detected_account:
            _update_env_file(detected_qmt_path, detected_account)

    # ---- 6. .env config ----------------------------------------------------
    print()
    print(".env configuration:")

    try:
        from dotenv import dotenv_values
        cfg = dotenv_values(".env")
    except Exception:
        cfg = {}

    if not cfg:
        _check(".env file", False,
               "copy .env.example to .env and edit it (or run scripts\\setup.bat)")
        failures += 1
    else:
        _check(".env file", True)

        required = ["QMT_RPYC_HOST", "QMT_RPYC_PORT", "QMT_RPYC_AUTH_KEY", "QMT_PATH"]
        for key in required:
            val = cfg.get(key, "")
            ok = bool(val) and val not in (
                "your-secret-key-here", "your-account-id",
                r"C:\path\to\MiniQMT\userdata_mini",
            )
            detail = ""
            if not ok and not val:
                detail = f"{key} is not set"
            elif not ok:
                detail = f"{key} has placeholder value"
            if not _check(f"  {key}", ok, detail, fatal=(key != "QMT_PATH")):
                failures += 1

        # QMT_ACCOUNT_ID is optional (only used if trader needs account auto-wrap)
        acc_val = cfg.get("QMT_ACCOUNT_ID", "")
        if acc_val and acc_val != "your-account-id":
            _check("  QMT_ACCOUNT_ID", True)
        elif miniqmt_running and detected_account:
            _check(f"  QMT_ACCOUNT_ID (auto-detected: {detected_account})", True)
        else:
            _check("  QMT_ACCOUNT_ID", bool(acc_val and acc_val != "your-account-id"),
                   "set your account ID in .env, or start MiniQMT logged in and re-run",
                   fatal=False)

    # ---- summary -----------------------------------------------------------
    print()
    if failures == 0:
        if miniqmt_running and xtquant_ok:
            print("All critical checks passed. MiniQMT is running — ready to start.")
        else:
            print("All critical checks passed.")
        return 0
    else:
        print(f"{failures} critical check(s) failed. Fix them and re-run.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
