"""
main.py
────────
지하공동구 이상감지 시스템 진입점.

실행:
    python main.py
    python main.py --once   # 1회 평가 후 종료 (테스트/CI용)
"""
from __future__ import annotations

import argparse
import signal
import sys

from engine.orchestrator import Orchestrator
from utils.logger import get_logger

log = get_logger("main")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="지하공동구 이상감지 엔진")
    parser.add_argument(
        "--once",
        action="store_true",
        help="1회 평가 후 종료 (기본: 무한 루프)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    orchestrator = Orchestrator()

    # SIGINT / SIGTERM 처리
    def _shutdown(signum, frame):
        log.info("종료 신호(%s) 수신 — 안전 종료 중...", signum)
        orchestrator.stop()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if args.once:
            log.info("단회 평가 모드 실행")
            orchestrator.run_once()
            log.info("단회 평가 완료")
        else:
            orchestrator.run()
    except Exception as e:
        log.critical("치명적 오류로 엔진 종료: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()
