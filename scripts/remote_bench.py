#!/usr/bin/env python3
"""Read-only comparison of serial and batch option-detail queries."""
import argparse
import time
from qmt_rpyc import QmtClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--underlying', default='510050.SH')
    args = parser.parse_args()
    with QmtClient.connect_profile(args.profile) as client:
        dates = client.options.get_expiry_dates(args.underlying)
        if not dates.dates:
            print('No current option contracts')
            return
        chain = client.options.get_option_chain(args.underlying, dates.dates[0])
        codes = chain.contract_codes[:100]
        start = time.perf_counter()
        serial = {code: client.options.get_contract_details([code]).require_all()[code] for code in codes}
        serial_time = time.perf_counter() - start
        start = time.perf_counter()
        batch = client.options.get_contract_details(codes).require_all()
        batch_time = time.perf_counter() - start
        assert serial == batch
        print(f'{len(codes)} contracts: serial={serial_time:.3f}s batch={batch_time:.3f}s')


if __name__ == '__main__':
    main()
