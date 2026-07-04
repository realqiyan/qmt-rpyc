"""Install xtquant from QMT directory into current venv.

Usage: python scripts/install_xtquant.py <QMT_PATH>
Writes a .pth file pointing at QMT's site-packages directory.
"""
import sys
import os


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

    # --- verify xtquant modules exist ---
    xtquant_marker = os.path.join(qmt_site, "xtquant")
    xtdata_marker = os.path.join(qmt_site, "xtdata.py")
    if not os.path.exists(xtquant_marker) and not os.path.exists(xtdata_marker):
        print(f"[ERROR] xtquant modules not found under: {qmt_site}")
        print(f"        Checked: xtquant/ and xtdata.py — neither exists.")
        sys.exit(1)

    # --- write .pth file ---
    venv_dir = os.path.dirname(os.path.dirname(sys.executable))
    site_packages = os.path.join(venv_dir, "Lib", "site-packages")
    os.makedirs(site_packages, exist_ok=True)
    pth = os.path.join(site_packages, "qmt_xtquant.pth")
    with open(pth, "w") as f:
        f.write(qmt_site + "\n")
    print(f"Wrote {pth} -> {qmt_site}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/install_xtquant.py <QMT_PATH>")
        print("Example: python scripts/install_xtquant.py D:\\ACT\\userdata_mini")
        sys.exit(1)
    main(sys.argv[1])
