"""Windows QMT environment discovery used by the server CLI."""

import ctypes
import ipaddress
import os
import socket
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path


def get_miniqmt_process():
    try:
        import psutil
    except ImportError:
        return None
    for proc in psutil.process_iter(["name", "exe", "pid"]):
        try:
            if proc.info["name"] == "XtMiniQmt.exe":
                exe = proc.info.get("exe") or ""
                return {
                    "exe": exe,
                    "pid": proc.info["pid"],
                    "install_dir": os.path.dirname(exe) if exe else "",
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return None


def get_window_title(pid):
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    visible = []
    hidden = []

    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
    )

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if title:
                target = visible if user32.IsWindowVisible(hwnd) else hidden
                target.append(title)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return (visible or hidden or [None])[0]


def extract_account(title):
    if title and " - " in title:
        value = title.split(" - ", 1)[0].strip()
        if value.isdigit():
            return value
    return None


def find_xtquant_site(qmt_dir):
    qmt_dir = os.path.abspath(qmt_dir)
    candidates = [
        os.path.join(qmt_dir, "Lib", "site-packages"),
        qmt_dir,
    ]
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "xtquant")):
            return candidate
    try:
        for root, directories, _files in os.walk(qmt_dir):
            depth = root[len(qmt_dir):].count(os.sep)
            if depth > 3:
                directories.clear()
                continue
            if "xtquant" in directories:
                return root
    except OSError:
        return None
    return None


def detect_environment():
    result = {
        "miniqmt": None,
        "qmt_path": None,
        "account_id": None,
        "xtquant_site": None,
    }
    process = get_miniqmt_process()
    result["miniqmt"] = process
    if not process:
        return result

    install_dir = process.get("install_dir") or ""
    parent = os.path.dirname(install_dir.rstrip("\\/"))
    qmt_path = os.path.join(parent, "userdata_mini")
    if os.path.isdir(qmt_path):
        result["qmt_path"] = qmt_path
    result["account_id"] = extract_account(
        get_window_title(process["pid"])
    )
    result["xtquant_site"] = (
        find_xtquant_site(install_dir)
        or (find_xtquant_site(parent) if parent else None)
    )
    return result


def private_ipv4_addresses():
    addresses = set()
    try:
        infos = socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        )
        for info in infos:
            value = info[4][0]
            address = ipaddress.ip_address(value)
            if address.is_private and not address.is_loopback:
                addresses.add(value)
    except OSError:
        pass
    return sorted(addresses)


def managed_xtquant_path():
    return Path(sys.prefix) / "Lib" / "site-packages" / "xtquant"


def wire_xtquant(xtquant_site):
    if sys.platform != "win32":
        raise RuntimeError("xtquant junctions are supported only on Windows")
    source = Path(xtquant_site) / "xtquant"
    target = managed_xtquant_path()
    if not source.is_dir():
        raise FileNotFoundError("xtquant source not found: {}".format(source))
    if target.exists():
        try:
            if os.path.samefile(str(source), str(target)):
                return target
        except OSError:
            pass
        raise FileExistsError(
            "target already exists and will not be replaced: {}".format(
                target
            )
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(target), str(source)],
        capture_output=True,
        text=True,
        shell=False,
    )
    if result.returncode:
        raise RuntimeError(
            "mklink failed: {}".format(
                (result.stderr or result.stdout).strip()
            )
        )
    return target
