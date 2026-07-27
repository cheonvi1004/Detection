"""
main.py — 지하공동구 이상감지 시스템 진입점

실행:
    python main.py            # 무한 루프
    python main.py --once     # 1회 평가 후 종료 (테스트/CI)
"""
from __future__ import annotations
import argparse, signal, sys
from engine.orchestrator import Orchestrator
from utils.logger import get_logger

log = get_logger("main")


def main() -> None:
    parser = argparse.ArgumentParser(description="지하공동구 이상감지 엔진")
    parser.add_argument("--once", action="store_true", help="1회 평가 후 종료")
    args = parser.parse_args()

    orchestrator = Orchestrator()

    def _shutdown(signum, frame):
        log.info("종료 신호(%s) — 안전 종료 중...", signum)
        orchestrator.stop()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if args.once:
            orchestrator.run_once()
        else:
            orchestrator.run()
    except Exception as e:
        log.critical("치명적 오류: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()
