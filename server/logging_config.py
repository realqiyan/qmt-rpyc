import logging
from logging.handlers import TimedRotatingFileHandler
import os


def setup_logging(log_dir="logs"):
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_qmt_rpyc_handler", False):
            return

    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "rpyc_server.log"),
        when="midnight", backupCount=7, encoding="utf-8")
    file_handler.setFormatter(fmt)

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)

    root.setLevel(logging.INFO)
    file_handler._qmt_rpyc_handler = True
    console._qmt_rpyc_handler = True
    root.addHandler(file_handler)
    root.addHandler(console)
