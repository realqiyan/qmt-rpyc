import logging
from logging.handlers import TimedRotatingFileHandler
import os


def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "rpyc_server.log"),
        when="midnight", backupCount=30, encoding="utf-8")
    file_handler.setFormatter(fmt)

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
