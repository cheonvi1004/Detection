import time
import requests
from typing import Optional
from config.settings import settings
from domain.models import DomainResult
from domain.enums import AlertLevel
from utils.logger import get_logger

log = get_logger(__name__)

class EventNotifier:
    def __init__(self):
        # 환경 변수(settings)에서 가져오되, 값이 없으면 요청하신 기본값 사용
        self.api_url = getattr(settings, "EVENT_API_URL", "https://kong.spaasta.com/event-api")
        self.auth_token = getattr(settings, "EVENT_API_AUTH", "Basic YWRtaW46Y29ucGl0YTc3ISE=")

    def send_alert(self, result: DomainResult):
        """
        이상 감지 결과를 이벤트 중계 API로 전송합니다.
        위험 단계(AlertLevel)가 NONE(정상)이 아닐 때만 발송합니다.
        """
        if result.level == AlertLevel.NONE:
            return

        # 현재 시간을 Unix Timestamp(초 단위 정수)로 변환
        now_ts = int(time.time())
        
        # objectId 추출: 이벤트를 유발한 센서가 있다면 첫 번째 센서 ID 사용, 없으면 "-"
        object_id = "-"
        if result.triggered_sensors and len(result.triggered_sensors) > 0:
            object_id = result.triggered_sensors[0]

        # evtClass 추출: FIRE, FLOOD, STRUCTURE, CONDENSATION (Enum의 name 속성 활용)
        evt_class = result.domain.name

        # evtLevel 추출: 숫자형 변환 (예: LEVEL_4 -> 4)
        evt_level = self._extract_level_number(result.level)

        # 요청하신 JSON 규격에 맞춘 페이로드 구성
        payload = {
            "resourceId": result.resource_id,
            "objectId": object_id,
            "objectInstance": "-",
            "startTime": now_ts,
            "transTime": now_ts,
            "evtClass": evt_class,
            "evtAttribute": "ANOMALY",
            "evtElementType": 4,
            "evtInst": "-",
            "evtLevel": evt_level,
            "evtMessage": result.detail
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": self.auth_token
        }

        try:
            # API POST 요청 전송 (타임아웃 5초 설정)
            response = requests.post(
                self.api_url, 
                json=payload, 
                headers=headers, 
                timeout=5
            )
            response.raise_for_status()  # 200번대 응답이 아닐 경우 예외 발생
            log.info(f"[이벤트 발송 성공] {evt_class} | {result.resource_id} | objectId: {object_id} | Level: {evt_level}")
            
        except requests.exceptions.RequestException as e:
            log.error(f"[이벤트 발송 실패] {e} | Payload: {payload}")

    def _extract_level_number(self, level: AlertLevel) -> int:
        """AlertLevel Enum에서 숫자(1~4)만 추출하여 반환합니다."""
        try:
            # 보통 Enum 값이 정수이거나 'LEVEL_1' 형태의 문자열일 수 있으므로 안전하게 처리
            if isinstance(level.value, int):
                return level.value
            if isinstance(level.value, str) and "LEVEL_" in level.name:
                return int(level.name.split("_")[1])
            return 0 # 기본값
        except Exception:
            return 0