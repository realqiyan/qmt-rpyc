"""Install xtquant from QMT directory into current venv.

Usage: python scripts/install_xtquant.py <QMT_PATH>

Creates a directory junction (mklink /J) from the venv's site-packages/xtquant
to QMT's xtquant directory.  This exposes ONLY xtquant — unlike the old .pth
approach which added the entire QMT site-packages (including incompatible
packages like pyreadline) to sys.path.
"""
import sys
import os
import subprocess


def _rm_junction(path):
    """Remove a directory junction (or regular dir) if it exists."""
    if os.path.isdir(path):
        # rmdir works for both junctions and real directories on Windows
        subprocess.run(
            ["cmd.exe", "/c", "rmdir", path],
            capture_output=True, shell=False,
        )


def _create_junction(src, dst):
    """Create a directory junction: dst -> src.  src must exist."""
    subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", dst, src],
        capture_output=True, shell=False, check=True,
    )
    print(f"Created junction: {dst} -> {src}")


def main(qmt_path):
    # --- validate QMT_PATH exists ---
    if not os.path.isdir(qmt_path):
        print(f"[ERROR] QMT_PATH does not exist: {qmt_path}")
        print(f"        Make sure QMT/MiniQMT is installed correctly.")
        sys.exit(1)

    # --- compute source site-packages ---
    qmt_site = os.path.abspath(
        os.path.join(qmt_path, "..", "bin.x64", "Lib", "site-packages")
    )

    if not os.path.isdir(qmt_site):
        print(f"[ERROR] xtquant site-packages not found at: {qmt_site}")
        print(f"        QMT_PATH was: {qmt_path}")
        print(f"        Expected structure: <QMT_PATH>\\..\\bin.x64\\Lib\\site-packages")
        sys.exit(1)

    # --- verify xtquant directory exists ---
    qmt_xtquant = os.path.join(qmt_site, "xtquant")
    if not os.path.isdir(qmt_xtquant):
        print(f"[ERROR] xtquant/ directory not found under: {qmt_site}")
        sys.exit(1)

    # --- determine target in venv site-packages ---
    venv_dir = os.path.dirname(os.path.dirname(sys.executable))
    site_packages = os.path.join(venv_dir, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)
    venv_xtquant = os.path.join(site_packages, "xtquant")

    # --- remove old .pth file (legacy wiring) ---
    old_pth = os.path.join(site_packages, "qmt_xtquant.pth")
    if os.path.isfile(old_pth):
        os.remove(old_pth)
        print(f"Removed legacy .pth file: {old_pth}")

    # --- remove existing junction / directory ---
    _rm_junction(venv_xtquant)

    # --- create junction ---
    _create_junction(qmt_xtquant, venv_xtquant)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/install_xtquant.py <QMT_PATH>")
        print("Example: python scripts/install_xtquant.py D:\\ACT\\userdata_mini")
        sys.exit(1)
    main(sys.argv[1])
