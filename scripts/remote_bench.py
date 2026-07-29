#!/usr/bin/env python3
"""Remote client benchmark for qmt-rpyc batch performance.

Usage:
    pip install qmt-rpyc                     # first time only
    python remote_bench.py                   # uses env vars below
    python remote_bench.py 192.168.1.100     # pass server IP as arg

Environment variables (all optional):
    QMT_RPYC_HOST      Server IP (default: 127.0.0.1, or first CLI arg)
    QMT_RPYC_PORT      Server port (default: 18812)
    QMT_RPYC_AUTH_KEY  HMAC auth key
"""
import time, os, sys

# ── config ──────────────────────────────────────────────────────────
HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QMT_RPYC_HOST", "127.0.0.1")
PORT = int(os.environ.get("QMT_RPYC_PORT", "18812"))
AUTH_KEY = os.environ.get("QMT_RPYC_AUTH_KEY")

UNDERLYING = "159915.SZ"
EXPIRY = "20260722"
SERIAL_SAMPLE = 20
TIMEOUT = 120


def hr(seconds):
    if seconds >= 60:
        return f"{seconds:.0f}s"
    if seconds >= 1:
        return f"{seconds:.2f}s"
    return f"{seconds * 1000:.0f}ms"


def banner(title):
    print("\n" + "=" * 56)
    print(f"  {title}")
    print("=" * 56)


def main():
    print(f"Server: {HOST}:{PORT}")
    print(f"Auth:   {'on' if AUTH_KEY else 'off'}")
    print(f"Target: {UNDERLYING}  expiry={EXPIRY}")

    # ── connect ───────────────────────────────────────────────────
    t0 = time.time()
    try:
        from qmt_rpyc import QmtClient
    except ImportError:
        print("\nERROR: qmt-rpyc not installed.")
        print("  pip install qmt-rpyc")
        sys.exit(1)

    try:
        client = QmtClient.connect(HOST, PORT, auth_key=AUTH_KEY, timeout=TIMEOUT)
    except Exception as e:
        print(f"\nERROR connecting: {e}")
        sys.exit(1)

    t_conn = time.time() - t0
    health = client.health()
    print(f"Connected ({hr(t_conn)}), trader={'OK' if health.get('trader_available') else 'NO'}, "
          f"uptime={hr(health.get('uptime_seconds', 0))}")

    # storage for summary
    t_batch = None
    ok_batch = 0
    err_batch = 0
    t_serial_per_call = None
    n_options = 0

    try:
        # ── step 1: get option list ──────────────────────────────
        banner("Step 1: get_option_undl_data + get_option_list")

        t0 = time.time()
        codes = client.xtdata.get_option_undl_data(UNDERLYING)
        t1 = time.time()
        if isinstance(codes, dict):
            codes = list(codes.keys())
        codes = list(codes or [])
        print(f"  [{hr(t1 - t0)}] get_option_undl_data  → {len(codes)} codes")

        t0 = time.time()
        options = client.xtdata.get_option_list(UNDERLYING, EXPIRY, "")
        t2 = time.time()
        if isinstance(options, dict):
            options = list(options.keys())
        options = list(options or [])
        print(f"  [{hr(t2 - t0)}] get_option_list      → {len(options)} options @ {EXPIRY}")

        if not options:
            print("  WARNING: no options for this expiry, using all codes")
            options = codes[:50]

        n_options = len(options)

        # ── step 2: batch detail ─────────────────────────────────
        banner(f"Step 2: batch get_option_detail_data × {n_options}")

        t0 = time.time()
        results = client.xtdata.get_option_detail_data.batch([
            ((code,), {}) for code in options
        ])
        t_batch = time.time() - t0
        ok_batch = sum(1 for r in results if r.get("status") == "ok")
        err_batch = n_options - ok_batch

        print(f"  [{hr(t_batch)}] total")
        print(f"  ok={ok_batch}  err={err_batch}")
        print(f"  {t_batch / n_options * 1000:.0f}ms/call effective")
        if t_batch > 0:
            print(f"  {n_options / t_batch:.0f} calls/s")

        # show first 3
        print("  Sample:")
        for r in results[:3]:
            d = r.get("data", {})
            if d:
                print(f"    {d.get('InstrumentID', '?')}  "
                      f"strike={d.get('OptExercisePrice', '?')}  "
                      f"type={d.get('optType', '?')}  "
                      f"expire={d.get('ExpireDate', '?')}")

        # ── step 3: serial baseline ──────────────────────────────
        sample = options[:SERIAL_SAMPLE]
        banner(f"Step 3: serial baseline ({len(sample)} calls)")

        t0 = time.time()
        ok_s = 0
        for code in sample:
            try:
                d = client.xtdata.get_option_detail_data(code)
                if d:
                    ok_s += 1
            except Exception:
                pass
        t_serial = time.time() - t0
        t_serial_per_call = t_serial / len(sample) * 1000
        t_projected = t_serial_per_call * n_options / 1000

        print(f"  [{hr(t_serial)}] {len(sample)} calls → ok={ok_s}")
        print(f"  {t_serial_per_call:.0f}ms/call")
        print(f"  projected {n_options} calls: {t_projected:.1f}s")

        # ── summary ──────────────────────────────────────────────
        banner("Summary")
        print(f"  Server:         {HOST}:{PORT}")
        print(f"  Options:        {n_options}")
        print(f"  Per-call (LAN): {t_serial_per_call:.0f}ms")
        print(f"  Batch total:    {hr(t_batch)} ({ok_batch}/{n_options} ok)")

        if t_batch and t_serial_per_call:
            t_proj = t_serial_per_call * n_options / 1000
            speedup = t_proj / t_batch if t_batch > 0 else 0
            print(f"  Projected ser.: {t_proj:.1f}s")
            print(f"  Speedup:        {speedup:.1f}x")
            if speedup >= 10:
                print(f"  ✓  批量调用有效加速 (batch vs 逐个串行)")
            elif speedup >= 2:
                print(f"  ~  有加速但低于预期，可能局域网延迟占比较高")
            else:
                print(f"  △  加速不明显，串行调用已经很快，无需 batch")

    finally:
        client.close()
        print("\nDisconnected.")


if __name__ == "__main__":
    main()
