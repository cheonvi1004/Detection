"""
alert/notifier.py
──────────────────
알림 발송 + cooldown 관리.

설계서 4.2절 기준:
  L1: 시스템 로그
  L2: 로그 + SMS + Push
  L3: L2 + 경보음
  L4: L3 + 전관방송 + 재난 지원 요청
"""
from __future__ import annotations
import json, time, urllib.request
from datetime import datetime

from config.settings import settings
from domain.enums import AlertLevel
from domain.models import ZoneStatus
from utils.logger import get_logger

log = get_logger(__name__)


class Notifier:
    def __init__(self) -> None:
        # cooldown 추적: (resource_id, level) → 마지막 발송 unix ts
        self._last: dict[tuple[str, int], float] = {}

    def notify(self, status: ZoneStatus) -> list[str]:
        lvl = status.zone_level
        if lvl == AlertLevel.NONE:
            return []
        if self._in_cooldown(status.resource_id, lvl):
            log.debug("[%s] cooldown 중 스킵 (%s)", status.resource_id, lvl.label)
            return []
        actions = self._dispatch(status.resource_id, lvl, status)
        self._last[(status.resource_id, lvl.value)] = time.time()
        return actions

    def notify_recovery(self, resource_id: str, prev: AlertLevel) -> None:
        msg = f"[{resource_id}] 이상 해제 — {prev.label} → 정상"
        log.info("🟢 %s", msg)
        self._sms(resource_id, msg)

    # ── 단계별 발송 ───────────────────────────────────────────────
    def _dispatch(self, resource_id: str, lvl: AlertLevel, status: ZoneStatus) -> list[str]:
        msg  = self._build_msg(resource_id, lvl, status)
        acts = []

        if lvl >= AlertLevel.LEVEL_1:
            log.info("🔵 [%s] %s", lvl.label, msg)
            acts.append("LOG")

        if lvl >= AlertLevel.LEVEL_2:
            self._sms(resource_id, msg)
            self._push(resource_id, msg, lvl)
            acts += ["SMS", "PUSH"]

        if lvl >= AlertLevel.LEVEL_3:
            self._alarm(resource_id)
            acts.append("ALARM")

        if lvl >= AlertLevel.LEVEL_4:
            self._broadcast(resource_id, msg)
            self._disaster(resource_id)
            acts += ["BROADCAST", "DISASTER_SUPPORT"]

        return acts

    @staticmethod
    def _build_msg(resource_id: str, lvl: AlertLevel, status: ZoneStatus) -> str:
        triggered = [
            f"{d.value}({r.level.label})"
            for d, r in status.domain_results.items()
            if r.level > AlertLevel.NONE
        ]
        return (
            f"[{lvl.label}] {resource_id} | "
            f"{', '.join(triggered) or '이상감지'} | "
            f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

    def _sms(self, resource_id: str, msg: str) -> None:
        if not settings.alert.sms_url:
            log.debug("[%s] SMS 미설정 → 로그 대체: %s", resource_id, msg)
            return
        try:
            data = json.dumps({"resource_id": resource_id, "message": msg}).encode()
            req  = urllib.request.Request(
                settings.alert.sms_url, data=data,
                headers={"Content-Type": "application/json",
                         "X-API-Key": settings.alert.sms_api_key},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                log.info("[%s] SMS 완료: HTTP %s", resource_id, r.status)
        except Exception as e:
            log.error("[%s] SMS 실패: %s", resource_id, e)

    def _push(self, resource_id: str, msg: str, lvl: AlertLevel) -> None:
        if not settings.alert.push_url:
            return
        try:
            data = json.dumps({
                "to": f"/topics/{resource_id}",
                "notification": {"title": f"[{lvl.label}] 공동구 이상", "body": msg},
                "data": {"resource_id": resource_id, "level": lvl.value},
            }).encode()
            req = urllib.request.Request(
                settings.alert.push_url, data=data,
                headers={"Content-Type": "application/json",
                         "Authorization": f"key={settings.alert.push_key}"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                log.info("[%s] Push 완료: HTTP %s", resource_id, r.status)
        except Exception as e:
            log.error("[%s] Push 실패: %s", resource_id, e)

    def _alarm(self, resource_id: str) -> None:
        log.warning("🚨 [%s] 경보음 트리거", resource_id)
        # TODO: BAS REST API 호출

    def _broadcast(self, resource_id: str, msg: str) -> None:
        log.critical("📢 [%s] 전관방송: %s", resource_id, msg)
        # TODO: 방송 시스템 연동

    def _disaster(self, resource_id: str) -> None:
        log.critical("🆘 [%s] 재난 소관부서 지원 요청", resource_id)
        # TODO: 재난관리 시스템 연동

    def _in_cooldown(self, resource_id: str, lvl: AlertLevel) -> bool:
        last = self._last.get((resource_id, lvl.value))
        return last is not None and (time.time() - last) < settings.alert.cooldown_sec
