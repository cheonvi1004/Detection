-- ============================================================
-- 지하공동구 이상감지 시스템 — 임계값 관리 DDL v2.0
-- DB: PostgreSQL 14+
--
-- [v2 변경사항]
--   1. 결로: Murray(1967) Magnus-Tetens 공식 계수 테이블 추가
--            건구온도-노점온도 차(ΔT) 기반 3단계(관심/주의/경계) 구성
--   2. 침수: 공동구별 집수정 물리 파라미터(집수정 용량·유입관 높이·펌프 용량)
--            관리자 설정 테이블 추가
--   3. 공동구 현황 엑셀 기반 실제 운영 현장(31개소) 샘플 데이터 반영
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ════════════════════════════════════════════════════════════
-- 0. 공통 열거 타입
-- ════════════════════════════════════════════════════════════

CREATE TYPE detection_domain AS ENUM (
    'FIRE_GAS',
    'FLOOD',
    'CONDENSATION',
    'STRUCTURE'
);

CREATE TYPE alert_level AS ENUM (
    'LEVEL_1',   -- 관심
    'LEVEL_2',   -- 주의
    'LEVEL_3',   -- 경계
    'LEVEL_4'    -- 심각 (결로는 3단계이므로 미사용)
);

CREATE TYPE threshold_op AS ENUM (
    'GTE',   -- >=
    'LTE',   -- <=
    'GT',    -- >
    'LT'     -- <
);

CREATE TYPE aggregation_fn AS ENUM (
    'LAST',
    'MEAN',
    'MAX',
    'DERIVATIVE'
);


-- ════════════════════════════════════════════════════════════
-- 1. 구역(Zone) 마스터
--    엑셀 31개 공동구 현황 기반 설계
-- ════════════════════════════════════════════════════════════
CREATE TABLE zones (
    zone_id         VARCHAR(30)  PRIMARY KEY,
    zone_name       VARCHAR(100) NOT NULL,
    region          VARCHAR(50),            -- 시도 (서울, 경기도, 인천 ...)
    location_desc   TEXT,
    has_ventilation BOOLEAN      NOT NULL DEFAULT TRUE,   -- 환기설비 보유 여부
    has_drainage    BOOLEAN      NOT NULL DEFAULT TRUE,   -- 배수설비 보유 여부
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  zones                IS '공동구 관리 구역 마스터 (엑셀 31개 현장 기준)';
COMMENT ON COLUMN zones.has_ventilation IS '환기설비 미보유 구역은 결로 자동제어 명령 불가 (여의도 등)';

-- 엑셀 현황 기반 샘플 데이터 (주요 현장)
INSERT INTO zones (zone_id, zone_name, region, has_ventilation, has_drainage) VALUES
    ('SEOUL-MOKDONG',    '목동 공동구',      '서울',   TRUE,  TRUE),
    ('SEOUL-YEOUIDO',    '여의도 공동구',    '서울',   FALSE, TRUE),   -- 환기 X
    ('SEOUL-GAEPO',      '개포 공동구',      '서울',   TRUE,  TRUE),
    ('SEOUL-GARAK',      '가락 공동구',      '서울',   TRUE,  TRUE),
    ('SEOUL-SANGGYE',    '상계 공동구',      '서울',   TRUE,  TRUE),
    ('SEOUL-SANGAM',     '상암 공동구',      '서울',   TRUE,  TRUE),
    ('SEOUL-EUNPYEONG',  '은평 공동구',      '서울',   TRUE,  TRUE),
    ('SEOUL-MAGOK',      '마곡 공동구',      '서울',   TRUE,  TRUE),
    ('GG-BUCHEON',       '부천 공동구',      '경기도', TRUE,  TRUE),
    ('GG-BUNDANG',       '분당 공동구',      '경기도', TRUE,  TRUE),
    ('GG-PYEONGCHON',    '평촌 공동구',      '경기도', TRUE,  TRUE),
    ('GG-ILSAN',         '일산 공동구',      '경기도', TRUE,  TRUE),
    ('GG-PAJU',          '파주 공동구',      '경기도', TRUE,  TRUE),
    ('GG-SANBON',        '산본 공동구',      '경기도', TRUE,  TRUE),
    ('GG-SUWON',         '수원 공동구',      '경기도', TRUE,  TRUE),
    ('GG-ANSAN',         '안산 공동구',      '경기도', TRUE,  TRUE),
    ('INCHEON-YEONSU',   '연수구 공동구',    '인천',   TRUE,  TRUE),
    ('INCHEON-SORAE',    '소래논현 공동구',  '인천',   TRUE,  TRUE),
    ('INCHEON-SONGDO13', '송도1,3공구',      '인천',   TRUE,  TRUE),
    ('INCHEON-SONGDO57', '송도5,7공구',      '인천',   TRUE,  TRUE),
    ('INCHEON-SONGDO8',  '송도8공구',        '인천',   TRUE,  TRUE),
    ('DJ-DUNSAN',        '둔산 공동구',      '대전',   TRUE,  TRUE),
    ('SJ-SEJONG',        '세종시 공동구',    '세종',   TRUE,  TRUE),
    ('CB-OCHANG',        '오창 공동구',      '충북',   TRUE,  TRUE),
    ('CN-NAEPO',         '내포 공동구',      '충남',   TRUE,  TRUE),
    ('GJ-SANGMU',        '상무 공동구',      '광주',   TRUE,  TRUE),
    ('JN-YEOSU',         '여수 공동구',      '전남',   TRUE,  TRUE),
    ('BS-HAEUNDAE',      '해운대 공동구',    '부산',   TRUE,  TRUE),
    ('GB-GUMI',          '구미 공동구',      '경북',   FALSE, TRUE),  -- 원격제어 X
    ('GB-ANDONG',        '안동 공동구',      '경북',   TRUE,  TRUE),
    ('GN-CHANGWON',      '창원 공동구',      '경남',   TRUE,  TRUE);


-- ════════════════════════════════════════════════════════════
-- 2. 결로 — Magnus-Tetens 공식 계수 테이블
--
--    Murray(1967) / Magnus-Tetens 수식:
--      es(T) = a * exp( b*T / (c+T) )   -- 포화수증기압 (Pa)
--      e      = RH/100 * es              -- 실제 수증기압
--      Td     = c * ln(e/a) / (b - ln(e/a))  -- 노점온도 (℃)
--
--    온도 범위에 따라 계수(a,b,c)가 달라지므로 테이블로 관리
-- ════════════════════════════════════════════════════════════
CREATE TABLE condensation_formula_coefficients (
    coeff_id        SMALLSERIAL  PRIMARY KEY,
    temp_range_desc VARCHAR(60)  NOT NULL,   -- 온도 범위 설명
    temp_min_c      NUMERIC(6,2),            -- 적용 온도 하한 (℃), NULL = -∞
    temp_max_c      NUMERIC(6,2),            -- 적용 온도 상한 (℃), NULL = +∞
    a               NUMERIC(12,4) NOT NULL,  -- 포화수증기압 계수 a (Pa)
    b               NUMERIC(10,6) NOT NULL,  -- 지수 분자 계수 b
    c               NUMERIC(10,6) NOT NULL,  -- 지수 분모 계수 c (℃)
    reference       VARCHAR(200) NOT NULL DEFAULT 'Murray (1967), J. Appl. Meteor.',
    is_default      BOOLEAN      NOT NULL DEFAULT FALSE,  -- 단일 계수 사용 시 TRUE
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE condensation_formula_coefficients IS
    'Magnus-Tetens 노점온도 계산 공식 계수 (Murray 1967). '
    'es(T) = a * exp(b*T / (c+T))  →  Td = c*ln(e/a) / (b - ln(e/a))';
COMMENT ON COLUMN condensation_formula_coefficients.a IS '포화수증기압 계수 (Pa). 수식에 따라 610.78 또는 611.2 사용';
COMMENT ON COLUMN condensation_formula_coefficients.b IS '지수 분자 계수. 물(액체) 17.2694, 얼음 21.8746';
COMMENT ON COLUMN condensation_formula_coefficients.c IS '지수 분모 계수. 물(액체) 237.29℃, 얼음 265.49℃';

-- Murray(1967) 표준 계수 삽입
-- ① 물(액체) 기준: 0℃ 이상 일반 환경 → 공동구 주 사용
INSERT INTO condensation_formula_coefficients
    (temp_range_desc, temp_min_c, temp_max_c, a, b, c, is_default)
VALUES
    ('물(액체) 기준, 0℃ 이상 (공동구 일반 환경)',
     0, NULL, 610.78, 17.2694, 237.29, TRUE),
-- ② 얼음 기준: 0℃ 미만 동절기 극저온
    ('얼음 기준, 0℃ 미만 (동절기 극저온)',
     NULL, 0, 610.78, 21.8746, 265.49, FALSE);


-- ════════════════════════════════════════════════════════════
-- 3. 결로 — 공동구별 단계 임계값 (ΔT 기준)
--
--    ΔT = 건구온도(T_dry) - 노점온도(T_dew)
--    ΔT ≤ X  → 관심(LEVEL_1)
--    ΔT ≤ Y  → 주의(LEVEL_2), Y < X
--    ΔT ≤ 0  → 경계(LEVEL_3, 결로 발생)
--
--    개별 공동구마다 환기 응답 속도·구조가 달라
--    X, Y 값을 현장에서 직접 조정 가능하도록 설계
-- ════════════════════════════════════════════════════════════
CREATE TABLE condensation_thresholds (
    cond_threshold_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id             VARCHAR(30)  REFERENCES zones(zone_id) ON DELETE CASCADE,
                        -- NULL = 전체 기본값 (zone_id IS NULL AND is_default = TRUE)
    coeff_id            SMALLINT     NOT NULL
                            REFERENCES condensation_formula_coefficients(coeff_id),

    -- 단계 경계값 (ΔT = T_dry - T_dew, 단위 ℃)
    level1_delta_t      NUMERIC(5,2) NOT NULL,   -- 관심 상한 X (예: 5.0℃)
    level2_delta_t      NUMERIC(5,2) NOT NULL,   -- 주의 상한 Y (예: 3.0℃, Y < X)
    -- 경계(LEVEL_3): ΔT ≤ 0 (결로 발생) — 고정값이므로 별도 컬럼 불필요

    -- 계절별 환기 기준 (외기온도 T_ext 기준 계절 구분)
    season_winter_max_c   NUMERIC(5,2) NOT NULL DEFAULT 12.0,  -- 겨울: T_ext ≤ 이 값
    season_spring_max_c   NUMERIC(5,2) NOT NULL DEFAULT 23.0,  -- 봄/가을: T_ext ≤ 이 값
    -- 여름: T_ext > season_spring_max_c

    -- 내부 목표 환경 (계절별)
    target_temp_winter_c  NUMERIC(5,2) NOT NULL DEFAULT 20.0,
    target_temp_spring_c  NUMERIC(5,2) NOT NULL DEFAULT 22.5,
    target_temp_summer_c  NUMERIC(5,2) NOT NULL DEFAULT 25.0,
    target_rh_winter_pct  NUMERIC(5,2) NOT NULL DEFAULT 60.0,
    target_rh_spring_pct  NUMERIC(5,2) NOT NULL DEFAULT 65.0,
    target_rh_summer_pct  NUMERIC(5,2) NOT NULL DEFAULT 70.0,

    is_default    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_to   TIMESTAMPTZ,
    updated_by    VARCHAR(60),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    note          TEXT,

    CONSTRAINT ck_cond_level_order CHECK (level2_delta_t < level1_delta_t),
    CONSTRAINT ck_cond_level2_positive CHECK (level2_delta_t > 0),
    CONSTRAINT uq_cond_zone_active UNIQUE (zone_id, is_active)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE  condensation_thresholds            IS '결로 감지 단계 임계값 (공동구별 ΔT 설정)';
COMMENT ON COLUMN condensation_thresholds.zone_id    IS 'NULL이면 전체 기본값(is_default=TRUE)으로 사용';
COMMENT ON COLUMN condensation_thresholds.level1_delta_t IS '관심 단계 ΔT 상한 X (℃). ΔT ≤ X 이면 관심';
COMMENT ON COLUMN condensation_thresholds.level2_delta_t IS '주의 단계 ΔT 상한 Y (℃). ΔT ≤ Y 이면 주의 (Y < X 제약)';

-- 기본값 삽입 (zone_id NULL = 전체 공통 기본값)
INSERT INTO condensation_thresholds (
    zone_id, coeff_id,
    level1_delta_t, level2_delta_t,
    is_default, note
) VALUES (
    NULL, 1,
    5.0, 3.0,
    TRUE,
    'Murray(1967) 물 기준 계수, 관심 ΔT≤5℃, 주의 ΔT≤3℃, 경계 ΔT≤0℃ (결로 발생)'
);

CREATE INDEX idx_cond_thresh_zone ON condensation_thresholds (zone_id, is_active);


-- ════════════════════════════════════════════════════════════
-- 4. 결로 임계값 변경 이력
-- ════════════════════════════════════════════════════════════
CREATE TABLE condensation_threshold_audit (
    log_id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    cond_threshold_id UUID         NOT NULL,
    zone_id           VARCHAR(30),
    action            VARCHAR(10)  NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE')),
    old_level1_delta_t NUMERIC(5,2),
    new_level1_delta_t NUMERIC(5,2),
    old_level2_delta_t NUMERIC(5,2),
    new_level2_delta_t NUMERIC(5,2),
    changed_by        VARCHAR(60)  NOT NULL DEFAULT SESSION_USER,
    changed_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    change_reason     TEXT
);

CREATE OR REPLACE FUNCTION fn_cond_threshold_audit()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO condensation_threshold_audit
            (cond_threshold_id, zone_id, action, new_level1_delta_t, new_level2_delta_t, changed_by)
        VALUES (NEW.cond_threshold_id, NEW.zone_id, 'INSERT',
                NEW.level1_delta_t, NEW.level2_delta_t, COALESCE(NEW.updated_by, SESSION_USER));
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO condensation_threshold_audit
            (cond_threshold_id, zone_id, action,
             old_level1_delta_t, new_level1_delta_t,
             old_level2_delta_t, new_level2_delta_t, changed_by)
        VALUES (NEW.cond_threshold_id, NEW.zone_id, 'UPDATE',
                OLD.level1_delta_t, NEW.level1_delta_t,
                OLD.level2_delta_t, NEW.level2_delta_t, SESSION_USER);
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO condensation_threshold_audit
            (cond_threshold_id, zone_id, action, old_level1_delta_t, old_level2_delta_t, changed_by)
        VALUES (OLD.cond_threshold_id, OLD.zone_id, 'DELETE',
                OLD.level1_delta_t, OLD.level2_delta_t, SESSION_USER);
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_cond_threshold_audit
AFTER INSERT OR UPDATE OR DELETE ON condensation_thresholds
FOR EACH ROW EXECUTE FUNCTION fn_cond_threshold_audit();


-- ════════════════════════════════════════════════════════════
-- 5. 침수 — 공동구별 물리 파라미터
--
--    집수정 용량, 유입관 높이, 배수펌프 용량은
--    공동구마다 달라 관리자가 현장별로 직접 설정
-- ════════════════════════════════════════════════════════════
CREATE TABLE flood_zone_parameters (
    param_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id               VARCHAR(30)  NOT NULL UNIQUE
                              REFERENCES zones(zone_id) ON DELETE CASCADE,

    -- ── 집수정 (Sump Pit) ──────────────────────────────────
    sump_capacity_liters  NUMERIC(10,2),        -- 집수정 총 용량 (L)
    sump_count            SMALLINT DEFAULT 1,   -- 집수정 개수

    -- ── 수위 기준점 (현장별 설정) ─────────────────────────
    -- L2 트리거: 수위 센서값 ≥ inlet_pipe_height_mm
    -- L3 트리거: 수위 센서값 ≥ inlet_pipe_height_mm + level3_offset_mm
    inlet_pipe_height_mm  NUMERIC(8,2),         -- 유입관 높이 기준 (mm)
    level2_offset_mm      NUMERIC(8,2) NOT NULL DEFAULT 0,    -- L2 오프셋 (기본 0 = 유입관 높이)
    level3_offset_mm      NUMERIC(8,2) NOT NULL DEFAULT 150,  -- L3 오프셋 (기본 +150mm)

    -- ── 배수 설비 ──────────────────────────────────────────
    pump_count            SMALLINT,             -- 배수펌프 대수
    pump_capacity_lpm     NUMERIC(10,2),        -- 펌프 1대당 배수 용량 (L/min)
    pump_total_capacity_lpm NUMERIC(10,2)       -- 전체 펌프 최대 배수량 (L/min)
        GENERATED ALWAYS AS (pump_count * pump_capacity_lpm) STORED,

    -- ── 경보 기준 (유입량 vs 배수량) ─────────────────────
    -- L4(배수불능): 유입량 > 전체 펌프 배수량
    drain_disabled_margin_pct NUMERIC(5,2) NOT NULL DEFAULT 10.0,
    -- 배수불능 판정 마진: 유입량 > 배수량 * (1 + margin/100) 시 L4

    -- ── 메타 ───────────────────────────────────────────────
    is_verified     BOOLEAN      NOT NULL DEFAULT FALSE,  -- 현장 실측값 검증 여부
    verified_by     VARCHAR(60),
    verified_at     TIMESTAMPTZ,
    note            TEXT,
    created_by      VARCHAR(60),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by      VARCHAR(60),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  flood_zone_parameters IS '침수 감지 공동구별 물리 파라미터 (집수정·펌프·유입관 높이)';
COMMENT ON COLUMN flood_zone_parameters.inlet_pipe_height_mm IS
    '수위 센서 기준 유입관 상단까지의 높이(mm). L2 트리거 기준점. NULL이면 감지 불가';
COMMENT ON COLUMN flood_zone_parameters.level3_offset_mm IS
    '유입관 높이에서 L3 트리거까지 추가 수위(mm). 기본 150mm(+15cm)';
COMMENT ON COLUMN flood_zone_parameters.pump_total_capacity_lpm IS
    '생성 컬럼: pump_count × pump_capacity_lpm. L4(배수불능) 판정 기준';
COMMENT ON COLUMN flood_zone_parameters.drain_disabled_margin_pct IS
    '배수불능 판정 안전 마진(%). 유입량 > 배수량*(1+margin/100) 시 L4 발령';
COMMENT ON COLUMN flood_zone_parameters.is_verified IS
    'FALSE: 기본값 또는 미확인 현장. TRUE: 현장 실측으로 검증 완료 (여의도·송도6공구 등 미확인 구역 구분)';

-- 현재 파악된 일부 현장 기본값 삽입 (여의도·송도6공구는 is_verified=FALSE로 미확인 표시)
INSERT INTO flood_zone_parameters
    (zone_id, sump_capacity_liters, pump_count, pump_capacity_lpm,
     inlet_pipe_height_mm, level3_offset_mm, is_verified, note)
VALUES
    ('SEOUL-YEOUIDO', NULL, NULL, NULL, NULL, 150, FALSE,
     '집수정 정보 미확인 — 현장 실측 필요'),
    ('INCHEON-SONGDO57', NULL, NULL, NULL, NULL, 150, FALSE,
     '송도6공구 집수정 정보 미확인 — 현장 실측 필요');

CREATE INDEX idx_flood_param_zone ON flood_zone_parameters (zone_id);


-- ════════════════════════════════════════════════════════════
-- 6. 침수 파라미터 변경 감사 이력
-- ════════════════════════════════════════════════════════════
CREATE TABLE flood_param_audit (
    log_id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    param_id              UUID         NOT NULL,
    zone_id               VARCHAR(30)  NOT NULL,
    action                VARCHAR(10)  NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE')),
    old_inlet_height_mm   NUMERIC(8,2),
    new_inlet_height_mm   NUMERIC(8,2),
    old_level3_offset_mm  NUMERIC(8,2),
    new_level3_offset_mm  NUMERIC(8,2),
    old_pump_count        SMALLINT,
    new_pump_count        SMALLINT,
    old_pump_capacity_lpm NUMERIC(10,2),
    new_pump_capacity_lpm NUMERIC(10,2),
    changed_by            VARCHAR(60)  NOT NULL DEFAULT SESSION_USER,
    changed_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    change_reason         TEXT
);

CREATE OR REPLACE FUNCTION fn_flood_param_audit()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO flood_param_audit
            (param_id, zone_id, action,
             new_inlet_height_mm, new_level3_offset_mm,
             new_pump_count, new_pump_capacity_lpm, changed_by)
        VALUES (NEW.param_id, NEW.zone_id, 'INSERT',
                NEW.inlet_pipe_height_mm, NEW.level3_offset_mm,
                NEW.pump_count, NEW.pump_capacity_lpm,
                COALESCE(NEW.created_by, SESSION_USER));
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO flood_param_audit
            (param_id, zone_id, action,
             old_inlet_height_mm, new_inlet_height_mm,
             old_level3_offset_mm, new_level3_offset_mm,
             old_pump_count, new_pump_count,
             old_pump_capacity_lpm, new_pump_capacity_lpm, changed_by)
        VALUES (NEW.param_id, NEW.zone_id, 'UPDATE',
                OLD.inlet_pipe_height_mm, NEW.inlet_pipe_height_mm,
                OLD.level3_offset_mm, NEW.level3_offset_mm,
                OLD.pump_count, NEW.pump_count,
                OLD.pump_capacity_lpm, NEW.pump_capacity_lpm, SESSION_USER);
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO flood_param_audit
            (param_id, zone_id, action,
             old_inlet_height_mm, old_level3_offset_mm,
             old_pump_count, old_pump_capacity_lpm, changed_by)
        VALUES (OLD.param_id, OLD.zone_id, 'DELETE',
                OLD.inlet_pipe_height_mm, OLD.level3_offset_mm,
                OLD.pump_count, OLD.pump_capacity_lpm, SESSION_USER);
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_flood_param_audit
AFTER INSERT OR UPDATE OR DELETE ON flood_zone_parameters
FOR EACH ROW EXECUTE FUNCTION fn_flood_param_audit();


-- ════════════════════════════════════════════════════════════
-- 7. 센서 유형 마스터
-- ════════════════════════════════════════════════════════════
CREATE TABLE sensor_types (
    sensor_type_id  VARCHAR(30)      PRIMARY KEY,
    domain          detection_domain NOT NULL,
    sensor_name     VARCHAR(100)     NOT NULL,
    unit            VARCHAR(20),
    influx_field    VARCHAR(60)      NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE sensor_types IS '센서 유형 마스터. influx_field = InfluxDB _field 키';

INSERT INTO sensor_types (sensor_type_id, domain, sensor_name, unit, influx_field, description) VALUES
    -- 화재/가스
    ('TEMP_ABSOLUTE',  'FIRE_GAS',     '절대 온도',           '℃',    'temperature',    NULL),
    ('TEMP_RATE',      'FIRE_GAS',     '온도 상승률',          '℃/min','temperature',    'Flux derivative(unit:1m)'),
    ('GAS_O2',         'FIRE_GAS',     '산소 농도',            '%',    'O2',             NULL),
    ('GAS_CO',         'FIRE_GAS',     '일산화탄소',           'ppm',  'CO',             NULL),
    ('GAS_CO2',        'FIRE_GAS',     '이산화탄소',           '%',    'CO2',            NULL),
    ('GAS_H2S',        'FIRE_GAS',     '황화수소',             'ppm',  'H2S',            NULL),
    -- 침수
    ('WATER_LEVEL',    'FLOOD',        '수위',                 'mm',   'water_level',    '집수정 수위 센서'),
    ('FLOW_INFLOW',    'FLOOD',        '유입량',               'L/min','inflow_rate',    NULL),
    ('FLOW_DRAIN',     'FLOOD',        '배수량',               'L/min','drain_rate',     NULL),
    -- 결로 (Murray-Tetens 계산 입력)
    ('WALL_TEMP',      'CONDENSATION', '벽체 온도(건구온도)',   '℃',   'wall_temp',      '결로 ΔT 계산 기준'),
    ('EXT_TEMP',       'CONDENSATION', '외기 온도',            '℃',   'ext_temperature', '계절 구분 기준'),
    ('EXT_HUMIDITY',   'CONDENSATION', '외기 상대습도',        '%',    'humidity',       'Magnus-Tetens 입력'),
    -- 구조
    ('CRACK_WIDTH',    'STRUCTURE',    '균열폭',               'mm',   'crack_width',    NULL),
    ('STRAIN',         'STRUCTURE',    '변형률',               'με',   'strain',         NULL),
    ('VIBRATION',      'STRUCTURE',    '진동가속도',           'cm/s', 'vibration',      '피크값(MAX) 사용');


-- ════════════════════════════════════════════════════════════
-- 8. 임계값 프로파일 + 구역 매핑 (화재/가스·침수·구조용)
-- ════════════════════════════════════════════════════════════
CREATE TABLE threshold_profiles (
    profile_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_name  VARCHAR(100) NOT NULL,
    description   TEXT,
    is_default    BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by    VARCHAR(60),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_profile_name UNIQUE (profile_name)
);

INSERT INTO threshold_profiles (profile_name, description, is_default)
VALUES ('DEFAULT', '표준 임계값 프로파일', TRUE);

CREATE TABLE zone_threshold_profiles (
    zone_id    VARCHAR(30) PRIMARY KEY REFERENCES zones(zone_id) ON DELETE CASCADE,
    profile_id UUID        NOT NULL REFERENCES threshold_profiles(profile_id) ON DELETE RESTRICT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by VARCHAR(60)
);


-- ════════════════════════════════════════════════════════════
-- 9. 핵심 임계값 테이블 (화재/가스·침수·구조)
--    결로는 condensation_thresholds 테이블을 별도 사용
-- ════════════════════════════════════════════════════════════
CREATE TABLE thresholds (
    threshold_id    UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id      UUID             NOT NULL REFERENCES threshold_profiles(profile_id),
    sensor_type_id  VARCHAR(30)      NOT NULL REFERENCES sensor_types(sensor_type_id),
    alert_level     alert_level      NOT NULL,
    operator        threshold_op     NOT NULL,
    threshold_value NUMERIC(12,4)    NOT NULL,
    aggregation     aggregation_fn   NOT NULL DEFAULT 'LAST',
    eval_window_sec INTEGER          NOT NULL DEFAULT 60,
    composite_group VARCHAR(30),
    composite_min_count SMALLINT     DEFAULT 1,
    description     TEXT,
    is_active       BOOLEAN          NOT NULL DEFAULT TRUE,
    effective_from  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    effective_to    TIMESTAMPTZ,
    created_by      VARCHAR(60),
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_active_threshold
        UNIQUE (profile_id, sensor_type_id, alert_level, is_active)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX idx_thresholds_profile ON thresholds (profile_id, is_active);
CREATE INDEX idx_thresholds_sensor  ON thresholds (sensor_type_id);


-- ════════════════════════════════════════════════════════════
-- 10. 임계값 변경 감사 이력
-- ════════════════════════════════════════════════════════════
CREATE TABLE threshold_audit_logs (
    log_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    threshold_id    UUID         NOT NULL,
    profile_id      UUID         NOT NULL,
    sensor_type_id  VARCHAR(30)  NOT NULL,
    alert_level     alert_level  NOT NULL,
    action          VARCHAR(10)  NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE')),
    old_value       NUMERIC(12,4),
    new_value       NUMERIC(12,4),
    old_operator    threshold_op,
    new_operator    threshold_op,
    old_is_active   BOOLEAN,
    new_is_active   BOOLEAN,
    changed_by      VARCHAR(60)  NOT NULL DEFAULT SESSION_USER,
    changed_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    change_reason   TEXT,
    ip_address      INET
);

CREATE INDEX idx_audit_threshold_id ON threshold_audit_logs (threshold_id);
CREATE INDEX idx_audit_changed_at   ON threshold_audit_logs (changed_at DESC);

CREATE OR REPLACE FUNCTION fn_threshold_audit()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO threshold_audit_logs
            (threshold_id, profile_id, sensor_type_id, alert_level, action,
             new_value, new_operator, new_is_active, changed_by)
        VALUES (NEW.threshold_id, NEW.profile_id, NEW.sensor_type_id, NEW.alert_level, 'INSERT',
                NEW.threshold_value, NEW.operator, NEW.is_active, COALESCE(NEW.created_by, SESSION_USER));
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO threshold_audit_logs
            (threshold_id, profile_id, sensor_type_id, alert_level, action,
             old_value, new_value, old_operator, new_operator, old_is_active, new_is_active, changed_by)
        VALUES (NEW.threshold_id, NEW.profile_id, NEW.sensor_type_id, NEW.alert_level, 'UPDATE',
                OLD.threshold_value, NEW.threshold_value, OLD.operator, NEW.operator,
                OLD.is_active, NEW.is_active, SESSION_USER);
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO threshold_audit_logs
            (threshold_id, profile_id, sensor_type_id, alert_level, action,
             old_value, old_operator, old_is_active, changed_by)
        VALUES (OLD.threshold_id, OLD.profile_id, OLD.sensor_type_id, OLD.alert_level, 'DELETE',
                OLD.threshold_value, OLD.operator, OLD.is_active, SESSION_USER);
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_threshold_audit
AFTER INSERT OR UPDATE OR DELETE ON thresholds
FOR EACH ROW EXECUTE FUNCTION fn_threshold_audit();


-- ════════════════════════════════════════════════════════════
-- 11. updated_at 자동 갱신 트리거
-- ════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_zones_upd         BEFORE UPDATE ON zones               FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_profiles_upd      BEFORE UPDATE ON threshold_profiles   FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_thresholds_upd    BEFORE UPDATE ON thresholds            FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_flood_param_upd   BEFORE UPDATE ON flood_zone_parameters FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();
CREATE TRIGGER trg_cond_thresh_upd   BEFORE UPDATE ON condensation_thresholds FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- ════════════════════════════════════════════════════════════
-- 12. 기본 임계값 데이터 삽입
-- ════════════════════════════════════════════════════════════
DO $$
DECLARE v_profile UUID;
BEGIN
    SELECT profile_id INTO v_profile FROM threshold_profiles WHERE is_default = TRUE LIMIT 1;

    INSERT INTO thresholds
        (profile_id, sensor_type_id, alert_level, operator, threshold_value,
         aggregation, eval_window_sec, description)
    VALUES
        -- ── 화재/가스 ──────────────────────────────────────
        (v_profile,'TEMP_RATE',     'LEVEL_2','GTE', 8,    'DERIVATIVE',60, '분당 온도 상승 8℃/min 이상'),
        (v_profile,'TEMP_ABSOLUTE', 'LEVEL_3','GTE', 60,   'LAST',      60, '절대온도 60℃ 이상'),
        (v_profile,'TEMP_ABSOLUTE', 'LEVEL_4','GTE', 75,   'LAST',      60, '절대온도 75℃ 이상'),
        (v_profile,'GAS_O2',        'LEVEL_2','LTE', 15,   'LAST',      60, 'O₂ 15% 이하'),
        (v_profile,'GAS_O2',        'LEVEL_3','LTE', 10,   'LAST',      60, 'O₂ 10% 이하'),
        (v_profile,'GAS_O2',        'LEVEL_4','LTE', 8,    'LAST',      60, 'O₂ 8% 이하'),
        (v_profile,'GAS_CO',        'LEVEL_2','GTE', 1400, 'LAST',      60, 'CO 1,400ppm 이상'),
        (v_profile,'GAS_CO',        'LEVEL_3','GTE', 2000, 'LAST',      60, 'CO 2,000ppm 이상'),
        (v_profile,'GAS_CO',        'LEVEL_4','GTE', 2500, 'LAST',      60, 'CO 2,500ppm 이상'),
        (v_profile,'GAS_CO2',       'LEVEL_2','GTE', 5,    'LAST',      60, 'CO₂ 5% 이상'),
        (v_profile,'GAS_CO2',       'LEVEL_3','GTE', 10,   'LAST',      60, 'CO₂ 10% 이상'),
        (v_profile,'GAS_CO2',       'LEVEL_4','GTE', 30,   'LAST',      60, 'CO₂ 30% 이상'),
        (v_profile,'GAS_H2S',       'LEVEL_2','GTE', 100,  'LAST',      60, 'H₂S 100ppm 이상'),
        (v_profile,'GAS_H2S',       'LEVEL_3','GTE', 200,  'LAST',      60, 'H₂S 200ppm 이상'),
        (v_profile,'GAS_H2S',       'LEVEL_4','GTE', 500,  'LAST',      60, 'H₂S 500ppm 이상'),
        -- ── 침수 (수위는 zone별 flood_zone_parameters 기준값 참조) ──
        (v_profile,'WATER_LEVEL',   'LEVEL_2','GTE', 0,    'MEAN',      300,'수위 유입관 높이 도달 — 실제값은 flood_zone_parameters.inlet_pipe_height_mm 사용'),
        (v_profile,'WATER_LEVEL',   'LEVEL_3','GTE', 150,  'MEAN',      300,'수위 유입관+15cm — 실제 오프셋은 flood_zone_parameters.level3_offset_mm 사용'),
        -- ── 구조 ───────────────────────────────────────────
        (v_profile,'CRACK_WIDTH',   'LEVEL_2','GTE', 0.1,  'MEAN',      300,'균열폭 0.1mm 이상'),
        (v_profile,'CRACK_WIDTH',   'LEVEL_3','GTE', 0.3,  'MEAN',      300,'균열폭 0.3mm 이상'),
        (v_profile,'CRACK_WIDTH',   'LEVEL_4','GTE', 0.5,  'MEAN',      300,'균열폭 0.5mm 이상'),
        (v_profile,'STRAIN',        'LEVEL_2','GT',  2000, 'MEAN',      300,'변형률 2,000με 초과'),
        (v_profile,'STRAIN',        'LEVEL_3','GT',  2500, 'MEAN',      300,'변형률 2,500με 초과'),
        (v_profile,'STRAIN',        'LEVEL_4','GT',  3000, 'MEAN',      300,'변형률 3,000με 초과'),
        (v_profile,'VIBRATION',     'LEVEL_2','GTE', 0.2,  'MAX',       300,'진동가속도 0.2cm/s 이상'),
        (v_profile,'VIBRATION',     'LEVEL_3','GTE', 0.5,  'MAX',       300,'진동가속도 0.5cm/s 이상'),
        (v_profile,'VIBRATION',     'LEVEL_4','GTE', 1.0,  'MAX',       300,'진동가속도 1.0cm/s 이상');
END;
$$;


-- ════════════════════════════════════════════════════════════
-- 13. 편의 뷰
-- ════════════════════════════════════════════════════════════

-- 13-A. 현재 유효 임계값 (화재/가스·침수·구조)
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
    t.description
FROM thresholds t
JOIN threshold_profiles p  ON p.profile_id     = t.profile_id
JOIN sensor_types       st ON st.sensor_type_id = t.sensor_type_id
WHERE t.is_active = TRUE AND p.is_active = TRUE
  AND (t.effective_to IS NULL OR t.effective_to > NOW())
ORDER BY p.profile_name, st.domain, t.alert_level;

COMMENT ON VIEW v_active_thresholds IS '현재 유효 임계값 — 감지 엔진에서 직접 사용 (결로 제외)';


-- 13-B. 결로 감지 엔진용 뷰
--       감지 엔진이 ΔT 계산에 필요한 모든 값을 한 번에 조회
CREATE VIEW v_condensation_config AS
SELECT
    z.zone_id,
    z.zone_name,
    z.has_ventilation,
    -- 구역 전용 설정 우선, 없으면 기본값
    COALESCE(zc.cond_threshold_id, dc.cond_threshold_id)  AS cond_threshold_id,
    COALESCE(zc.level1_delta_t,    dc.level1_delta_t)     AS level1_delta_t,
    COALESCE(zc.level2_delta_t,    dc.level2_delta_t)     AS level2_delta_t,
    -- Magnus-Tetens 계수
    COALESCE(fc2.a,  fc1.a)  AS coeff_a,
    COALESCE(fc2.b,  fc1.b)  AS coeff_b,
    COALESCE(fc2.c,  fc1.c)  AS coeff_c,
    -- 계절 구분 기준
    COALESCE(zc.season_winter_max_c, dc.season_winter_max_c)  AS season_winter_max_c,
    COALESCE(zc.season_spring_max_c, dc.season_spring_max_c)  AS season_spring_max_c,
    -- 계절별 목표 환경
    COALESCE(zc.target_temp_winter_c, dc.target_temp_winter_c) AS target_temp_winter_c,
    COALESCE(zc.target_temp_spring_c, dc.target_temp_spring_c) AS target_temp_spring_c,
    COALESCE(zc.target_temp_summer_c, dc.target_temp_summer_c) AS target_temp_summer_c,
    COALESCE(zc.target_rh_winter_pct, dc.target_rh_winter_pct) AS target_rh_winter_pct,
    COALESCE(zc.target_rh_spring_pct, dc.target_rh_spring_pct) AS target_rh_spring_pct,
    COALESCE(zc.target_rh_summer_pct, dc.target_rh_summer_pct) AS target_rh_summer_pct
FROM zones z
-- 구역 전용 결로 설정
LEFT JOIN condensation_thresholds zc
    ON zc.zone_id = z.zone_id AND zc.is_active = TRUE
    AND (zc.effective_to IS NULL OR zc.effective_to > NOW())
LEFT JOIN condensation_formula_coefficients fc2 ON fc2.coeff_id = zc.coeff_id
-- 기본 결로 설정
CROSS JOIN (
    SELECT * FROM condensation_thresholds WHERE zone_id IS NULL AND is_default = TRUE LIMIT 1
) dc
JOIN condensation_formula_coefficients fc1 ON fc1.coeff_id = dc.coeff_id
WHERE z.is_active = TRUE;

COMMENT ON VIEW v_condensation_config IS
    '결로 감지 엔진용 통합 뷰. 구역 전용 설정 > DEFAULT 순으로 적용. '
    'Magnus-Tetens 계수(a,b,c)와 ΔT 임계값을 한 번에 조회';


-- 13-C. 침수 감지 엔진용 뷰 — 수위 트리거 절대값 산출
CREATE VIEW v_flood_config AS
SELECT
    z.zone_id,
    z.zone_name,
    fp.sump_capacity_liters,
    fp.sump_count,
    fp.inlet_pipe_height_mm,
    -- L2 트리거 절대 수위 (mm)
    fp.inlet_pipe_height_mm + fp.level2_offset_mm      AS level2_trigger_mm,
    -- L3 트리거 절대 수위 (mm)
    fp.inlet_pipe_height_mm + fp.level3_offset_mm      AS level3_trigger_mm,
    fp.pump_count,
    fp.pump_capacity_lpm,
    fp.pump_total_capacity_lpm,
    fp.drain_disabled_margin_pct,
    -- L4 판정 기준: 유입량이 이 값 초과 시 배수불능
    fp.pump_total_capacity_lpm * (1 + fp.drain_disabled_margin_pct / 100.0)
        AS level4_inflow_threshold_lpm,
    fp.is_verified,
    CASE WHEN fp.inlet_pipe_height_mm IS NULL THEN '⚠ 유입관 높이 미설정 — 현장 실측 필요'
         WHEN NOT fp.is_verified             THEN '⚠ 미검증 값 — 현장 확인 권장'
         ELSE '✓ 검증완료'
    END AS config_status
FROM zones z
LEFT JOIN flood_zone_parameters fp ON fp.zone_id = z.zone_id
WHERE z.is_active = TRUE AND z.has_drainage = TRUE;

COMMENT ON VIEW v_flood_config IS
    '침수 감지 엔진용 통합 뷰. L2·L3 절대 수위 트리거값과 L4(배수불능) 판정 기준을 산출. '
    'inlet_pipe_height_mm가 NULL이면 해당 구역 수위 기반 감지 불가';


-- ════════════════════════════════════════════════════════════
-- 14. 권한 (운영 환경에 맞게 수정)
-- ════════════════════════════════════════════════════════════
-- GRANT SELECT ON v_active_thresholds, v_condensation_config, v_flood_config TO detection_engine;
-- GRANT SELECT, INSERT, UPDATE ON thresholds, condensation_thresholds, flood_zone_parameters TO threshold_admin;
-- GRANT SELECT ON threshold_audit_logs, condensation_threshold_audit, flood_param_audit TO threshold_admin;
