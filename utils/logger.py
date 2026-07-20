"""utils/logger.py — 구조화 로거"""
from __future__ import annotations
import logging, os, sys
from logging.handlers import RotatingFileHandler

def get_logger(name: str) -> logging.Logger:
    from config.settings import settings
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S"
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if settings.log_file:
        os.makedirs(os.path.dirname(settings.log_file), exist_ok=True)
        fh = RotatingFileHandler(
            settings.log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
