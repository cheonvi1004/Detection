"""alert/notifier.py — 알림 발송 + cooldown"""
from __future__ import annotations
import json, time, urllib.request, urllib.error
from datetime import datetime
from config.settings import settings
from domain.enums import AlertLevel, SensorCategory
from domain.models import ZoneStatus
from utils.logger import get_logger
from infra.pg_client import PgRepo

log = get_logger(__name__)
url = "http://localhost:38090/event-api/api/v1/push-event"

class Notifier:
    def __init__(self) -> None:
        self._last: dict[tuple[str,int], float] = {}   # (zone_id, level) → timestamp
        self.pg = PgRepo()
        self.pg.connect()

    def notify(self, status: ZoneStatus):
        lvl = status.zone_level
        if lvl == AlertLevel.NONE:
            return []
        if self._in_cooldown(status.zone_id, lvl):
            log.debug("[%s] cooldown 중 스킵 (level=%s)", status.zone_id, lvl.label)
            return []
        response = self._dispatch(status.zone_id, lvl, status)
        data = json.dumps(response).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json"
            },
            method="POST"
        )
        urllib.request.urlopen(req)

        self._last[(status.zone_id, lvl.value)] = time.time()
        return response

    def notify_recovery(self, zone_id: str, prev: AlertLevel) -> None:
        msg = f"[{zone_id}] 이상 해제 — {prev.label} → 정상"
        log.info("🟢 %s", msg)
        self._sms(zone_id, msg)

    def _dispatch(self, zone_id, lvl, status):
        # msg = self._msg(zone_id, lvl, status)
        thresholds = self.pg.get_thresholds()
        acts= []
        response = {}

        evtClass, evtMessage = '', ''
        gasList1, gasList2, gasList3 = [], [], [] # 각각 LEVEL_1, LEVEL_2, LEVEL_3 담기 위한 리스트
        condensList1, condensList2 = [], [] # 각각 LEVEL_1, LEVEL_2 담기 위한 리스트
        gasNum1, gasNum2, gasNum3 = 0, 0, 0
        condensNum1 = 0
        for threshold in thresholds:
            sensor_category = threshold['sensor_category']
            if sensor_category == SensorCategory.FIRE:
                evtClass = "FIRE"
                if lvl == 1 and threshold["alert_level"] == 'LEVEL_1':
                    evtMessage = f"화재 발생이 감지되었습니다. 분당 온도 상승 {threshold['threshold_value']}˚C/min 이상입니다."
                elif (lvl == 2 and threshold["alert_level"] == 'LEVEL_2') or (lvl == 3 and threshold["alert_level"] == 'LEVEL_3'):
                    evtMessage = f"화재 발생이 감지되었습니다. 절대온도 {threshold['threshold_value']}˚C 이상입니다."

            elif sensor_category == SensorCategory.GAS:
                evtClass = "GAS"
                if lvl == 1 and threshold["alert_level"] == 'LEVEL_1':
                    gasList1.append(threshold["description"])
                    gasNum1 += 1
                    if gasNum1 == 4: # O2, CO2, CO, H2S 임계치 값 모두 받으면 evtMessage 생성
                        evtMessage = f"화재 발생이 감지되었습니다. 현재 {gasList1[0]}, {gasList1[1]}, {gasList1[2]}, {gasList1[3]}입니다."
                elif lvl == 2 and threshold["alert_level"] == 'LEVEL_2':
                    gasList2.append(threshold["description"])
                    gasNum2 += 1
                    if gasNum2 == 4: # O2, CO2, CO, H2S 임계치 값 모두 받으면 evtMessage 생성
                        evtMessage = f"화재 발생이 감지되었습니다. 현재 {gasList2[0]}, {gasList2[1]}, {gasList2[2]}, {gasList2[3]}입니다."
                elif lvl == 3 and threshold["alert_level"] == 'LEVEL_3':
                    gasList3.append(threshold["description"])
                    gasNum3 += 1
                    if gasNum3 == 4: # O2, CO2, CO, H2S 임계치 값 모두 받으면 evtMessage 생성
                        evtMessage = f"화재 발생이 감지되었습니다. 현재 {gasList3[0]}, {gasList3[1]}, {gasList3[2]}, {gasList3[3]}입니다."

            elif sensor_category == SensorCategory.CONDENSATION:
                evtClass = "CONDENSATION"
                if lvl == 1 and threshold["alert_level"] == 'LEVEL_1':
                    condensList1.append(threshold["description"])
                    condensNum1 += 1
                    if condensNum1 == 2:
                        evtMessage = f"결로가 발생하였습니다. 현재 {condensList1[0]}, {condensList1[1]}입니다."
                elif lvl == 2 and threshold["alert_level"] == 'LEVEL_2':
                    condensList2.append(threshold["description"])
                    evtMessage = f"결로가 발생하였습니다. 현재 {condensList2[0]}입니다."

            elif sensor_category == SensorCategory.STRUCTURE: # 추후 변형률도 포함해야 한다면 로직 수정해야 함
                evtClass = "STRUCTURE"
                if (lvl == 1 and threshold["alert_level"] == 'LEVEL_1') or (lvl == 2 and threshold["alert_level"] == 'LEVEL_2')\
                        or (lvl == 3 and threshold["alert_level"] == 'LEVEL_3'):
                    evtMessage = f"균열이 발생하였습니다. 현재 {threshold['description']}입니다."


        if lvl >= AlertLevel.LEVEL_1:
            # log.info("🔵 [관심] %s", msg); acts.append("LOG")
            response = {"resourceId": "RESOURCE001",
                        "objectId": "SENSOR001",
                        "objectInstance": "-",   # 없으면 "-" 설정
                        "startTime": int(time.time()), # influx 시간 컬럼/1000으로 변경해야 함
                        "transTime": int(time.time()), # influx 시간 컬럼/1000으로 변경해야 함
                        "evtClass": evtClass,
                        "evtAttribute": "ANOMALY",
                        "evtElementType": 3,     # 1: 상태, 2: 임계치, 3: 이상탐지
                        "evtInst": "-",
                        "evtLevel": 1,           # 2: 관심, 3: 주의, 4: 경계, 5: 심각
                        "evtMessage": evtMessage
                        }

        if lvl >= AlertLevel.LEVEL_2:
            # self._sms(zone_id, msg); self._push(zone_id, msg, lvl)
            # acts += ["SMS","PUSH"]
            response = {"resourceId": "RESOURCE001",
                        "objectId": "SENSOR001",
                        "objectInstance": "-",  # 없으면 "-" 설정
                        "startTime": int(time.time()),  # influx 시간 컬럼/1000으로 변경해야 함
                        "transTime": int(time.time()),  # influx 시간 컬럼/1000으로 변경해야 함
                        "evtClass": evtClass,
                        "evtAttribute": "ANOMALY",
                        "evtElementType": 3,  # 1: 상태, 2: 임계치, 3: 이상탐지
                        "evtInst": "-",
                        "evtLevel": 2,  # 2: 관심, 3: 주의, 4: 경계, 5: 심각
                        "evtMessage": evtMessage
                        }
        if lvl >= AlertLevel.LEVEL_3:
            # self._alarm(zone_id); acts.append("ALARM")
            response = {"resourceId": "RESOURCE001",
                        "objectId": "SENSOR001",
                        "objectInstance": "-",  # 없으면 "-" 설정
                        "startTime": int(time.time()),  # influx 시간 컬럼/1000으로 변경해야 함
                        "transTime": int(time.time()),  # influx 시간 컬럼/1000으로 변경해야 함
                        "evtClass": evtClass,
                        "evtAttribute": "ANOMALY",
                        "evtElementType": 3,  # 1: 상태, 2: 임계치, 3: 이상탐지
                        "evtInst": "-",
                        "evtLevel": 3,  # 2: 관심, 3: 주의, 4: 경계, 5: 심각
                        "evtMessage": evtMessage
                        }
        if lvl >= AlertLevel.LEVEL_4:
            # self._broadcast(zone_id, msg); self._disaster(zone_id)
            # acts += ["BROADCAST","DISASTER_SUPPORT"]
            response = {"resourceId": "RESOURCE001",
                        "objectId": "SENSOR001",
                        "objectInstance": "-",  # 없으면 "-" 설정
                        "startTime": int(time.time()),  # influx 시간 컬럼/1000으로 변경해야 함
                        "transTime": int(time.time()),  # influx 시간 컬럼/1000으로 변경해야 함
                        "evtClass": evtClass,
                        "evtAttribute": "ANOMALY",
                        "evtElementType": 3,  # 1: 상태, 2: 임계치, 3: 이상탐지
                        "evtInst": "-",
                        "evtLevel": 4,  # 2: 관심, 3: 주의, 4: 경계, 5: 심각
                        "evtMessage": evtMessage
                        }
        return response

    # @staticmethod
    # def _msg(zone_id, lvl, status) -> str:
    #     triggered = [f"{d.value}({r.level.label})"
    #                  for d,r in status.domain_results.items() if r.level > AlertLevel.NONE]
    #     return (f"[{lvl.label}] {zone_id} | {', '.join(triggered) or '이상감지'} "
    #             f"| {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")

    def _sms(self, zone_id, msg) -> None:
        if not settings.alert.sms_url:
            log.debug("[%s] SMS 미설정 — 로그 대체: %s", zone_id, msg); return
        try:
            data = json.dumps({"zone_id":zone_id,"message":msg}).encode()
            req  = urllib.request.Request(settings.alert.sms_url, data=data,
                       headers={"Content-Type":"application/json",
                                "X-API-Key":settings.alert.sms_api_key}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                log.info("[%s] SMS 완료: %s", zone_id, r.status)
        except Exception as e:
            log.error("[%s] SMS 실패: %s", zone_id, e)

    # def _push(self, zone_id, msg, lvl) -> None:
    #     if not settings.alert.push_url:
    #         log.debug("[%s] Push 미설정 — 스킵", zone_id); return
    #     try:
    #         data = json.dumps({"to":f"/topics/{zone_id}",
    #                            "notification":{"title":f"[{lvl.label}] 공동구 이상",
    #                                            "body":msg}}).encode()
    #         req = urllib.request.Request(settings.alert.push_url, data=data,
    #                   headers={"Content-Type":"application/json",
    #                            "Authorization":f"key={settings.alert.push_key}"}, method="POST")
    #         with urllib.request.urlopen(req, timeout=5) as r:
    #             log.info("[%s] Push 완료: %s", zone_id, r.status)
    #     except Exception as e:
    #         log.error("[%s] Push 실패: %s", zone_id, e)
    #
    # def _alarm(self, zone_id):
    #     log.warning("🚨 [%s] 경보음 트리거", zone_id)  # TODO: BAS 연동
    #
    # def _broadcast(self, zone_id, msg):
    #     log.critical("📢 [%s] 전관방송: %s", zone_id, msg)  # TODO: 방송시스템 연동
    #
    # def _disaster(self, zone_id):
    #     log.critical("🆘 [%s] 재난 소관부서 지원 요청", zone_id)  # TODO: 재난관리시스템 연동
    #
    def _in_cooldown(self, zone_id, lvl) -> bool:
        last = self._last.get((zone_id, lvl.value))
        return last is not None and (time.time() - last) < settings.alert.cooldown_sec
