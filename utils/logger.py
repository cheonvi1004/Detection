"""utils/logger.py — 구조화 로거"""
from __future__ import annotations
import logging, os, sys
from logging.handlers import RotatingFileHandler

# def get_logger(name: str) -> logging.Logger:
#     from config.settings import settings
#     logger = logging.getLogger(name)
#     if logger.handlers:
#         return logger
#     level = getattr(logging, settings.log_level.upper(), logging.info)
#     logger.setLevel(level)
#     fmt = logging.Formatter(
#         "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
#         datefmt="%Y-%m-%dT%H:%M:%S"
#     )
#     ch = logging.StreamHandler(sys.stdout)
#     ch.setFormatter(fmt)
#     logger.addHandler(ch)
#     if settings.log_file:
#         os.makedirs(os.path.dirname(settings.log_file), exist_ok=True)
#         fh = RotatingFileHandler(
#             settings.log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
#         )
#         fh.setFormatter(fmt)
#         logger.addHandler(fh)
#     return logger

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s | %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )
        
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 💡 1. 환경 변수 'LOG_LEVEL'을 읽어옵니다. (값이 없으면 'WARNING'을 기본값으로 사용)
        raw_level = os.getenv("LOG_LEVEL", "WARNING").upper()
        
        # 💡 2. 문자열(예: "INFO")을 로깅 모듈의 실제 레벨 상수(logging.INFO)로 변환합니다.
        # getattr를 사용해 안전하게 변환하며, 오타가 있을 경우 WARNING으로 폴백(Fallback)합니다.
        level = getattr(logging, raw_level, logging.WARNING)
        
        # 💡 3. 동적으로 추출된 레벨을 로거에 적용합니다.
        logger.setLevel(level)
        
        logger.propagate = False

    return logger
