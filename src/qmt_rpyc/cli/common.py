"""Shared command-line presentation helpers."""

import argparse
import sys

from qmt_rpyc.version import __version__

EXIT_INTERRUPTED = 130


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Preserve command examples without adding noisy implicit defaults."""


class ArgumentParser(argparse.ArgumentParser):
    """Argument parser that includes actionable help in usage errors."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("formatter_class", HelpFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(2, "\nerror: {}\n".format(message))


def add_version_argument(parser):
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version="%(prog)s {}".format(__version__),
        help="show the installed qmt-rpyc version and exit",
    )


def emit_interrupted(emit, compact=False):
    emit(
        {
            "status": "interrupted",
            "message": "Interrupted by user (Ctrl-C).",
        },
        compact,
        sys.stderr,
    )
    return EXIT_INTERRUPTED
