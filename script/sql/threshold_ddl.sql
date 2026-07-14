-- ============================================================
-- 지하공동구 이상감지 시스템 — 임계값 관리 DDL
-- DB: PostgreSQL 14+
-- 설명: Zone별·센서 유형별 단계(L1~L4) 임계값 저장 및 변경 이력 관리
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- 0. 확장
-- ────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()


-- ────────────────────────────────────────────────────────────
-- 1. 공통 열거 타입
-- ────────────────────────────────────────────────────────────

-- 감지 영역
CREATE TYPE detection_domain AS ENUM (
    'FIRE_GAS',     -- 화재 / 가스
    'FLOOD',        -- 침수 / 수위
    'CONDENSATION', -- 결로
    'STRUCTURE'     -- 구조 안전
);

-- 이상 단계
CREATE TYPE alert_level AS ENUM (
    'LEVEL_1',  -- 관심
    'LEVEL_2',  -- 주의
    'LEVEL_3',  -- 경계
    'LEVEL_4'   -- 심각
);

-- 비교 연산자 (임계값 방향)
CREATE TYPE threshold_op AS ENUM (
    'GTE',   -- >= (이상)
    'LTE',   -- <= (이하)
    'GT',    -- >
    'LT'     -- <
);

-- 임계값 적용 집계 함수
CREATE TYPE aggregation_fn AS ENUM (
    'LAST',       -- 최신값
    'MEAN',       -- 평균
    'MAX',        -- 최대 (진동 피크 등)
    'DERIVATIVE'  -- 변화율 (온도 상승률 등)
);


-- ────────────────────────────────────────────────────────────
-- 2. 구역(Zone) 마스터
-- ────────────────────────────────────────────────────────────
CREATE TABLE zones (
    zone_id         VARCHAR(30)  PRIMARY KEY,
    zone_name       VARCHAR(100) NOT NULL,
    location_desc   TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  zones           IS '공동구 관리 구역 마스터';
COMMENT ON COLUMN zones.zone_id   IS '구역 식별자 (예: B2-SEC-03)';


-- ────────────────────────────────────────────────────────────
-- 3. 센서 유형 마스터
-- ────────────────────────────────────────────────────────────
CREATE TABLE sensor_types (
    sensor_type_id  VARCHAR(30)      PRIMARY KEY,
    domain          detection_domain NOT NULL,
    sensor_name     VARCHAR(100)     NOT NULL,
    unit            VARCHAR(20),                -- 측정 단위 (℃, ppm, mm, ...)
    influx_field    VARCHAR(60)      NOT NULL,  -- InfluxDB field key
    description     TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  sensor_types              IS '센서 유형 마스터';
COMMENT ON COLUMN sensor_types.influx_field IS 'InfluxDB _field 키 이름';

-- 기본 센서 유형 삽입
INSERT INTO sensor_types (sensor_type_id, domain, sensor_name, unit, influx_field) VALUES
    -- 화재 / 가스
    ('TEMP_ABSOLUTE',  'FIRE_GAS',     '절대 온도 센서',         '℃',   'temperature'),
    ('TEMP_RATE',      'FIRE_GAS',     '온도 상승률 (℃/min)',    '℃/min','temperature'),
    ('GAS_O2',         'FIRE_GAS',     '산소 농도 센서',          '%',   'O2'),
    ('GAS_CO',         'FIRE_GAS',     '일산화탄소 센서',         'ppm', 'CO'),
    ('GAS_CO2',        'FIRE_GAS',     '이산화탄소 센서',         '%',   'CO2'),
    ('GAS_H2S',        'FIRE_GAS',     '황화수소 센서',           'ppm', 'H2S'),
    -- 침수
    ('WATER_LEVEL',    'FLOOD',        '수위 센서',               'mm',  'water_level'),
    ('FLOW_INFLOW',    'FLOOD',        '유입량 센서',             'L/min','inflow_rate'),
    ('FLOW_DRAIN',     'FLOOD',        '배수량 센서',             'L/min','drain_rate'),
    -- 결로
    ('WALL_TEMP',      'CONDENSATION', '벽체 온도 센서',          '℃',   'wall_temp'),
    ('EXT_TEMP',       'CONDENSATION', '외기 온도 센서',          '℃',   'ext_temperature'),
    ('EXT_HUMIDITY',   'CONDENSATION', '외기 습도 센서',          '%',   'humidity'),
    -- 구조
    ('CRACK_WIDTH',    'STRUCTURE',    '균열계',                  'mm',  'crack_width'),
    ('STRAIN',         'STRUCTURE',    '변형률계',                'με',  'strain'),
    ('VIBRATION',      'STRUCTURE',    '진동가속도계',            'cm/s','vibration');


-- ────────────────────────────────────────────────────────────
-- 4. 임계값 프로파일 (논리 그룹)
--    기본(DEFAULT) 프로파일 외에 구역별 커스텀 프로파일 지원
-- ────────────────────────────────────────────────────────────
CREATE TABLE threshold_profiles (
    profile_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_name  VARCHAR(100) NOT NULL,
    description   TEXT,
    is_default    BOOLEAN      NOT NULL DEFAULT FALSE,  -- 구역 미지정 시 사용
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by    VARCHAR(60),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_profile_name UNIQUE (profile_name)
);

COMMENT ON TABLE  threshold_profiles           IS '임계값 프로파일. 구역별 커스텀 임계값 그룹';
COMMENT ON COLUMN threshold_profiles.is_default IS 'TRUE인 프로파일은 구역에 별도 지정이 없으면 자동 적용';

-- 기본 프로파일
INSERT INTO threshold_profiles (profile_name, description, is_default)
VALUES ('DEFAULT', '표준 임계값 프로파일', TRUE);


-- ────────────────────────────────────────────────────────────
-- 5. 구역-프로파일 매핑
--    한 구역에 활성 프로파일 1개만 허용
-- ────────────────────────────────────────────────────────────
CREATE TABLE zone_threshold_profiles (
    zone_id     VARCHAR(30) NOT NULL REFERENCES zones(zone_id)              ON DELETE CASCADE,
    profile_id  UUID        NOT NULL REFERENCES threshold_profiles(profile_id) ON DELETE RESTRICT,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by  VARCHAR(60),

    PRIMARY KEY (zone_id)  -- 구역당 1개 프로파일
);

COMMENT ON TABLE zone_threshold_profiles IS '구역별 활성 임계값 프로파일 매핑 (1구역 = 1프로파일)';


-- ────────────────────────────────────────────────────────────
-- 6. 핵심 임계값 테이블
-- ────────────────────────────────────────────────────────────
CREATE TABLE thresholds (
    threshold_id    UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID             NOT NULL REFERENCES threshold_profiles(profile_id) ON DELETE RESTRICT,
    sensor_type_id  VARCHAR(30)      NOT NULL REFERENCES sensor_types(sensor_type_id),
    alert_level     alert_level      NOT NULL,

    -- 임계값 조건
    operator        threshold_op     NOT NULL,  -- 비교 방향
    threshold_value NUMERIC(12, 4)   NOT NULL,  -- 임계값 수치
    aggregation     aggregation_fn   NOT NULL DEFAULT 'LAST',  -- 집계 방식
    eval_window_sec INTEGER          NOT NULL DEFAULT 60,  -- 평가 윈도우 (초)

    -- 복합 조건 (다른 센서와 AND 조건 시 사용)
    composite_group VARCHAR(30),        -- 같은 값끼리 AND 그룹
    composite_min_count SMALLINT DEFAULT 1,  -- 그룹 내 최소 충족 센서 수

    -- 메타
    description     TEXT,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    effective_from  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    effective_to    TIMESTAMPTZ,  -- NULL = 현재 유효
    created_by      VARCHAR(60),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- 동일 프로파일 내 센서-레벨 중복 방지 (활성 임계값 1개)
    CONSTRAINT uq_active_threshold
        UNIQUE (profile_id, sensor_type_id, alert_level, is_active)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE  thresholds                   IS '감지 영역별 단계별 임계값 정의';
COMMENT ON COLUMN thresholds.threshold_value   IS '임계값 수치 (단위는 sensor_types.unit 참조)';
COMMENT ON COLUMN thresholds.eval_window_sec   IS 'InfluxDB range 파라미터 (초 단위)';
COMMENT ON COLUMN thresholds.composite_group   IS '복합 조건 그룹명 — 같은 그룹 내 N개 이상 충족 시 레벨 상향';
COMMENT ON COLUMN thresholds.composite_min_count IS '복합 조건 최소 충족 수 (기본 1 = 단독 조건)';
COMMENT ON COLUMN thresholds.effective_to      IS 'NULL = 현재 유효 임계값';

-- 인덱스
CREATE INDEX idx_thresholds_profile   ON thresholds (profile_id, is_active);
CREATE INDEX idx_thresholds_sensor    ON thresholds (sensor_type_id);
CREATE INDEX idx_thresholds_level     ON thresholds (alert_level);


-- ────────────────────────────────────────────────────────────
-- 7. 임계값 변경 감사 이력
--    thresholds 행이 변경될 때마다 자동 기록
-- ────────────────────────────────────────────────────────────
CREATE TABLE threshold_audit_logs (
    log_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    threshold_id    UUID         NOT NULL,   -- 원본 threshold_id (삭제 후에도 보존)
    profile_id      UUID         NOT NULL,
    sensor_type_id  VARCHAR(30)  NOT NULL,
    alert_level     alert_level  NOT NULL,

    -- 변경 전후 스냅샷
    action          VARCHAR(10)  NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE')),
    old_value       NUMERIC(12, 4),
    new_value       NUMERIC(12, 4),
    old_operator    threshold_op,
    new_operator    threshold_op,
    old_aggregation aggregation_fn,
    new_aggregation aggregation_fn,
    old_is_active   BOOLEAN,
    new_is_active   BOOLEAN,

    -- 변경 컨텍스트
    changed_by      VARCHAR(60)  NOT NULL DEFAULT SESSION_USER,
    changed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    change_reason   TEXT,        -- 변경 사유 (관리자가 입력)
    ip_address      INET
);

COMMENT ON TABLE threshold_audit_logs IS '임계값 변경 감사 로그 (자동 기록, 삭제 불가)';

CREATE INDEX idx_audit_threshold_id ON threshold_audit_logs (threshold_id);
CREATE INDEX idx_audit_changed_at   ON threshold_audit_logs (changed_at DESC);
CREATE INDEX idx_audit_changed_by   ON threshold_audit_logs (changed_by);


-- ────────────────────────────────────────────────────────────
-- 8. 트리거 — 임계값 변경 시 자동 감사 로그 삽입
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION fn_threshold_audit()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO threshold_audit_logs (
            threshold_id, profile_id, sensor_type_id, alert_level,
            action,
            new_value, new_operator, new_aggregation, new_is_active,
            changed_by
        ) VALUES (
            NEW.threshold_id, NEW.profile_id, NEW.sensor_type_id, NEW.alert_level,
            'INSERT',
            NEW.threshold_value, NEW.operator, NEW.aggregation, NEW.is_active,
            COALESCE(NEW.created_by, SESSION_USER)
        );
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO threshold_audit_logs (
            threshold_id, profile_id, sensor_type_id, alert_level,
            action,
            old_value, new_value,
            old_operator, new_operator,
            old_aggregation, new_aggregation,
            old_is_active, new_is_active,
            changed_by
        ) VALUES (
            NEW.threshold_id, NEW.profile_id, NEW.sensor_type_id, NEW.alert_level,
            'UPDATE',
            OLD.threshold_value, NEW.threshold_value,
            OLD.operator, NEW.operator,
            OLD.aggregation, NEW.aggregation,
            OLD.is_active, NEW.is_active,
            SESSION_USER
        );
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO threshold_audit_logs (
            threshold_id, profile_id, sensor_type_id, alert_level,
            action,
            old_value, old_operator, old_aggregation, old_is_active,
            changed_by
        ) VALUES (
            OLD.threshold_id, OLD.profile_id, OLD.sensor_type_id, OLD.alert_level,
            'DELETE',
            OLD.threshold_value, OLD.operator, OLD.aggregation, OLD.is_active,
            SESSION_USER
        );
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_threshold_audit
AFTER INSERT OR UPDATE OR DELETE ON thresholds
FOR EACH ROW EXECUTE FUNCTION fn_threshold_audit();


-- ────────────────────────────────────────────────────────────
-- 9. updated_at 자동 갱신 트리거
-- ────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_zones_updated_at
BEFORE UPDATE ON zones
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_profiles_updated_at
BEFORE UPDATE ON threshold_profiles
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_thresholds_updated_at
BEFORE UPDATE ON thresholds
FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- ────────────────────────────────────────────────────────────
-- 10. 편의 뷰 — 현재 유효 임계값 (활성 + 만료되지 않은)
-- ────────────────────────────────────────────────────────────
CREATE VIEW v_active_thresholds AS
SELECT
    t.threshold_id,
    p.profile_name,
    st.domain,
    st.sensor_name,
    st.unit,
    st.influx_field,
    t.alert_level,
    t.operator,
    t.threshold_value,
    t.aggregation,
    t.eval_window_sec,
    t.composite_group,
    t.composite_min_count,
    t.description,
    t.effective_from
FROM thresholds t
JOIN threshold_profiles p  ON p.profile_id    = t.profile_id
JOIN sensor_types       st ON st.sensor_type_id = t.sensor_type_id
WHERE t.is_active    = TRUE
  AND p.is_active    = TRUE
  AND (t.effective_to IS NULL OR t.effective_to > NOW())
ORDER BY p.profile_name, st.domain, t.alert_level, st.sensor_type_id;

COMMENT ON VIEW v_active_thresholds IS '현재 유효한 임계값 전체 조회 (감지 엔진에서 직접 사용)';


-- ────────────────────────────────────────────────────────────
-- 11. 편의 뷰 — 구역별 적용 임계값
--     Zone에 프로파일이 지정된 경우 해당 프로파일,
--     미지정인 경우 DEFAULT 프로파일 임계값 반환
-- ────────────────────────────────────────────────────────────
CREATE VIEW v_zone_thresholds AS
SELECT
    z.zone_id,
    z.zone_name,
    COALESCE(zp.profile_id, dp.profile_id)  AS profile_id,
    COALESCE(p2.profile_name, dp.profile_name) AS profile_name,
    st.domain,
    st.sensor_name,
    st.unit,
    st.influx_field,
    t.alert_level,
    t.operator,
    t.threshold_value,
    t.aggregation,
    t.eval_window_sec,
    t.composite_group,
    t.composite_min_count
FROM zones z
-- 구역 전용 프로파일 (없으면 NULL)
LEFT JOIN zone_threshold_profiles zp ON zp.zone_id   = z.zone_id
LEFT JOIN threshold_profiles      p2 ON p2.profile_id = zp.profile_id
-- 기본 프로파일 (항상 존재)
CROSS JOIN (
    SELECT profile_id, profile_name FROM threshold_profiles
    WHERE is_default = TRUE AND is_active = TRUE
    LIMIT 1
) dp
-- 임계값: 구역 프로파일 우선, 없으면 기본
JOIN thresholds t ON t.profile_id = COALESCE(zp.profile_id, dp.profile_id)
                 AND t.is_active  = TRUE
                 AND (t.effective_to IS NULL OR t.effective_to > NOW())
JOIN sensor_types st ON st.sensor_type_id = t.sensor_type_id
WHERE z.is_active = TRUE
ORDER BY z.zone_id, st.domain, t.alert_level;

COMMENT ON VIEW v_zone_thresholds IS '구역별 실제 적용 임계값 조회 (구역 프로파일 > DEFAULT 프로파일 순)';


-- ────────────────────────────────────────────────────────────
-- 12. 기본 임계값 데이터 삽입 (DEFAULT 프로파일)
-- ────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_profile UUID;
BEGIN
    SELECT profile_id INTO v_profile FROM threshold_profiles WHERE is_default = TRUE LIMIT 1;

    INSERT INTO thresholds
        (profile_id, sensor_type_id, alert_level, operator, threshold_value, aggregation, eval_window_sec, description)
    VALUES
        -- ── 화재 / 가스 ──────────────────────────────────────
        -- 온도 상승률: ≥ 8℃/min → L2
        (v_profile, 'TEMP_RATE',     'LEVEL_2', 'GTE', 8,    'DERIVATIVE', 60,  '분당 온도 상승 8℃ 이상'),

        -- 절대 온도: ≥ 60℃ → L3, ≥ 75℃ → L4
        (v_profile, 'TEMP_ABSOLUTE', 'LEVEL_3', 'GTE', 60,   'LAST',       60,  '절대온도 60℃ 이상'),
        (v_profile, 'TEMP_ABSOLUTE', 'LEVEL_4', 'GTE', 75,   'LAST',       60,  '절대온도 75℃ 이상'),

        -- O₂: ≤ 15% → L2, ≤ 10% → L3, ≤ 8% → L4
        (v_profile, 'GAS_O2',        'LEVEL_2', 'LTE', 15,   'LAST',       60,  'O₂ 농도 15% 이하'),
        (v_profile, 'GAS_O2',        'LEVEL_3', 'LTE', 10,   'LAST',       60,  'O₂ 농도 10% 이하'),
        (v_profile, 'GAS_O2',        'LEVEL_4', 'LTE', 8,    'LAST',       60,  'O₂ 농도 8% 이하'),

        -- CO: ≥ 1400ppm → L2, ≥ 2000 → L3, ≥ 2500 → L4
        (v_profile, 'GAS_CO',        'LEVEL_2', 'GTE', 1400, 'LAST',       60,  'CO 농도 1,400ppm 이상'),
        (v_profile, 'GAS_CO',        'LEVEL_3', 'GTE', 2000, 'LAST',       60,  'CO 농도 2,000ppm 이상'),
        (v_profile, 'GAS_CO',        'LEVEL_4', 'GTE', 2500, 'LAST',       60,  'CO 농도 2,500ppm 이상'),

        -- CO₂: ≥ 5% → L2, ≥ 10% → L3, ≥ 30% → L4
        (v_profile, 'GAS_CO2',       'LEVEL_2', 'GTE', 5,    'LAST',       60,  'CO₂ 농도 5% 이상'),
        (v_profile, 'GAS_CO2',       'LEVEL_3', 'GTE', 10,   'LAST',       60,  'CO₂ 농도 10% 이상'),
        (v_profile, 'GAS_CO2',       'LEVEL_4', 'GTE', 30,   'LAST',       60,  'CO₂ 농도 30% 이상'),

        -- H₂S: ≥ 100ppm → L2, ≥ 200 → L3, ≥ 500 → L4
        (v_profile, 'GAS_H2S',       'LEVEL_2', 'GTE', 100,  'LAST',       60,  'H₂S 농도 100ppm 이상'),
        (v_profile, 'GAS_H2S',       'LEVEL_3', 'GTE', 200,  'LAST',       60,  'H₂S 농도 200ppm 이상'),
        (v_profile, 'GAS_H2S',       'LEVEL_4', 'GTE', 500,  'LAST',       60,  'H₂S 농도 500ppm 이상'),

        -- ── 침수 / 수위 ──────────────────────────────────────
        -- 수위: 유입관 높이 → L2 (기준 0mm 대비 상대값, 실제값은 Zone별 재정의 권장)
        (v_profile, 'WATER_LEVEL',   'LEVEL_2', 'GTE', 0,    'MEAN',       300, '수위 유입관 높이 도달 (Zone별 실제값 재설정 필요)'),
        (v_profile, 'WATER_LEVEL',   'LEVEL_3', 'GTE', 150,  'MEAN',       300, '수위 유입관+15cm 초과 (기준값 대비 150mm)'),

        -- ── 결로 ─────────────────────────────────────────────
        -- 상대습도: ≥ 60% → L1, ≥ 75% → L2
        (v_profile, 'EXT_HUMIDITY',  'LEVEL_1', 'GTE', 60,   'LAST',       600, '상대습도 60% 이상 (관심)'),
        (v_profile, 'EXT_HUMIDITY',  'LEVEL_2', 'GTE', 75,   'LAST',       600, '상대습도 75% 이상 (주의)'),

        -- 외기 온도: ≥ 30℃ → L1 (결로 관심 조건)
        (v_profile, 'EXT_TEMP',      'LEVEL_1', 'GTE', 30,   'LAST',       600, '외기온도 30℃ 이상 — 결로 관심'),

        -- ── 구조 안전 ─────────────────────────────────────────
        -- 균열: ≥ 0.1mm → L2, ≥ 0.3mm → L3, ≥ 0.5mm → L4
        (v_profile, 'CRACK_WIDTH',   'LEVEL_2', 'GTE', 0.1,  'MEAN',       300, '균열폭 0.1mm 이상'),
        (v_profile, 'CRACK_WIDTH',   'LEVEL_3', 'GTE', 0.3,  'MEAN',       300, '균열폭 0.3mm 이상'),
        (v_profile, 'CRACK_WIDTH',   'LEVEL_4', 'GTE', 0.5,  'MEAN',       300, '균열폭 0.5mm 이상'),

        -- 변형률: > 2000με → L2, > 2500 → L3, > 3000 → L4
        (v_profile, 'STRAIN',        'LEVEL_2', 'GT',  2000, 'MEAN',       300, '변형률 2,000με 초과'),
        (v_profile, 'STRAIN',        'LEVEL_3', 'GT',  2500, 'MEAN',       300, '변형률 2,500με 초과'),
        (v_profile, 'STRAIN',        'LEVEL_4', 'GT',  3000, 'MEAN',       300, '변형률 3,000με 초과'),

        -- 진동가속도: ≥ 0.2cm/s → L2, ≥ 0.5 → L3, ≥ 1.0 → L4 (피크값)
        (v_profile, 'VIBRATION',     'LEVEL_2', 'GTE', 0.2,  'MAX',        300, '진동가속도 0.2cm/s 이상'),
        (v_profile, 'VIBRATION',     'LEVEL_3', 'GTE', 0.5,  'MAX',        300, '진동가속도 0.5cm/s 이상'),
        (v_profile, 'VIBRATION',     'LEVEL_4', 'GTE', 1.0,  'MAX',        300, '진동가속도 1.0cm/s 이상');

END;
$$;


-- ────────────────────────────────────────────────────────────
-- 13. 권한 (예시 — 실 운영 환경에 맞게 수정)
-- ────────────────────────────────────────────────────────────

-- 감지 엔진: 임계값 읽기 전용
-- GRANT SELECT ON v_active_thresholds, v_zone_thresholds TO detection_engine;

-- 관리자: 임계값 CUD + 감사 로그 읽기
-- GRANT SELECT, INSERT, UPDATE ON thresholds TO threshold_admin;
-- GRANT SELECT ON threshold_audit_logs TO threshold_admin;

-- 조회 전용 사용자: 뷰만 접근
-- GRANT SELECT ON v_active_thresholds, v_zone_thresholds TO readonly_user;
