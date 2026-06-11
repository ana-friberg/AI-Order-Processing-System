import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """Return a logger with a stdout handler.  Idempotent — safe to call many times."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.propagate = False
    return logger
