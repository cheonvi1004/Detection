-- ============================================================
-- 지하공동구 이상감지 시스템 — 임계값 관리 DDL v2.0
-- DB: PostgreSQL 14+
--
-- [v2 변경사항]
--   1. 결로: Murray(1967) Magnus-Tetens 공식 계수 테이블 추가
--            건구온도-노점온도 차(ΔT) 기반 3단계(관심/주의/경계) 구성
--   2. 침수: 공동구별 집수정 물리 파라미터(집수정 용량·유입관 높이·펌프 용량)
--            관리자 설정 테이블 추가
--   3. 공동구 데이터 반영
-- ============================================================


-- ════════════════════════════════════════════════════════════
-- 0. 공통 열거 타입
-- ════════════════════════════════════════════════════════════

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
-- 결로 — Magnus-Tetens 공식 계수 테이블
--
--    Murray(1967) / Magnus-Tetens 수식:
--      es(T) = a * exp( b*T / (c+T) )   -- 포화수증기압 (Pa)
--      e      = RH/100 * es              -- 실제 수증기압
--      Td     = c * ln(e/a) / (b - ln(e/a))  -- 노점온도 (℃)
--
--    온도 범위에 따라 계수(a,b,c)가 달라지므로 테이블로 관리
-- ════════════════════════════════════════════════════════════
CREATE TABLE anomaly_condensation_formula_coefficients (
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

COMMENT ON TABLE anomaly_condensation_formula_coefficients IS
    'Magnus-Tetens 노점온도 계산 공식 계수 (Murray 1967). '
    'es(T) = a * exp(b*T / (c+T))  →  Td = c*ln(e/a) / (b - ln(e/a))';
COMMENT ON COLUMN anomaly_condensation_formula_coefficients.a IS '포화수증기압 계수 (Pa). 수식에 따라 6.1078 사용';
COMMENT ON COLUMN anomaly_condensation_formula_coefficients.b IS '지수 분자 계수. 물(액체) 17.2694';
COMMENT ON COLUMN anomaly_condensation_formula_coefficients.c IS '지수 분모 계수. 물(액체) 237.29℃';

-- Murray(1967) 표준 계수 삽입
-- ① 물(액체) 기준: 0℃ 이상 일반 환경 → 공동구 주 사용
INSERT INTO anomaly_condensation_formula_coefficients
    (temp_range_desc, temp_min_c, temp_max_c, a, b, c, is_default)
VALUES
    ('Murray(1967) 물 기준 공식',0, NULL, 6.1078, 17.2694, 237.29, TRUE);


-- ════════════════════════════════════════════════════════════
-- 결로 — 공동구별 단계 임계값 (ΔT 기준)
--
--    ΔT = 건구온도(T_dry) - 노점온도(T_dew)
--    ΔT ≤ X  → 관심(LEVEL_1)
--    ΔT ≤ Y  → 주의(LEVEL_2), Y < X
--    ΔT ≤ 0  → 경계(LEVEL_3, 결로 발생)
--
--    개별 공동구마다 환기 응답 속도·구조가 달라
--    X, Y 값을 현장에서 직접 조정 가능하도록 설계
-- ════════════════════════════════════════════════════════════
CREATE TABLE anomaly_condensation_thresholds (
    cond_threshold_id   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL = 전체 공통 기본값
    resource_id             VARCHAR(8),
    coeff_id            SMALLINT     NOT NULL,

    -- 단계 경계값 (ΔT = T_dry - T_dew, 단위 ℃)
    -- 주의 단계 기준
    level2_delta_t   NUMERIC(5,2) NOT NULL,

    updated_by    VARCHAR(60),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    note          TEXT,

    --주의 단계 임계값은 반드시 0보다 커야 한다
    CONSTRAINT ck_cond_level2_positive CHECK (level2_delta_t > 0),
    CONSTRAINT uq_cond_resource_active UNIQUE (resource_id, is_active)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE  anomaly_condensation_thresholds            IS '결로 감지 단계 임계값 (공동구별 ΔT 설정)';
COMMENT ON COLUMN anomaly_condensation_thresholds.resource_id    IS 'NULL이면 전체 기본값(is_default=TRUE)으로 사용';
COMMENT ON COLUMN anomaly_condensation_thresholds.level2_delta_t IS '주의 단계 ΔT 상한 Y (℃). ΔT ≤ Y 이면 주의';

-- 공동구별 1건
CREATE UNIQUE INDEX uq_cond_resource
ON anomaly_condensation_thresholds(resource_id)
WHERE resource_id IS NOT NULL;

-- 전체 기본값 1건
CREATE UNIQUE INDEX uq_cond_default
ON anomaly_condensation_thresholds((1))
WHERE resource_id IS NULL;

CREATE INDEX idx_cond_thresh_resource ON anomaly_condensation_thresholds (resource_id);

-- 기본값 삽입 (zone_id NULL = 전체 공통 기본값)
INSERT INTO anomaly_condensation_thresholds (
    resource_id, coeff_id,
    level2_delta_t,
    note
) VALUES (
    NULL, 1,
    5.0,
    'Murray(1967) 물 기준 계수, 주의 ΔT≤5℃, 경계 ΔT≤0℃ (결로 발생)'
);



-- ════════════════════════════════════════════════════════════
-- 침수 — 공동구별 물리 파라미터
--
--    집수정 용량, 유입관 높이, 배수펌프 용량은
--    공동구마다 달라 관리자가 현장별로 직접 설정
-- ════════════════════════════════════════════════════════════
CREATE TABLE anomaly_flood_parameters (
    param_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_id               VARCHAR(8)  NOT NULL UNIQUE
                              REFERENCES zones(zone_id) ON DELETE CASCADE,

    -- ── 집수정 (Sump Pit) ──────────────────────────────────
    sump_capacity_liters  NUMERIC(10,2),        -- 집수정 총 용량 (L)
    sump_height_mm        NUMERIC(8,2),          -- 집수정 높이(mm)
    sump_count            SMALLINT DEFAULT 1,   -- 집수정 개수

    -- ── 수위 기준점 (현장별 설정) ─────────────────────────
    -- L2 트리거: 수위 센서값 ≥ inlet_pipe_height_mm
    -- L3 트리거: 수위 센서값 ≥ inlet_pipe_height_mm + level3_offset_mm
    inlet_pipe_height_mm  NUMERIC(8,2),         -- 유입관 높이 기준 (mm)
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

COMMENT ON TABLE  anomaly_flood_parameters IS '침수 감지 공동구별 물리 파라미터 (집수정·펌프·유입관 높이)';
COMMENT ON COLUMN anomaly_flood_parameters.inlet_pipe_height_mm IS
    '수위 센서 기준 유입관 상단까지의 높이(mm). L2 트리거 기준점. NULL이면 감지 불가';
COMMENT ON COLUMN anomaly_flood_parameters.level3_offset_mm IS
    '유입관 높이에서 L3 트리거까지 추가 수위(mm). 기본 150mm(+15cm)';
COMMENT ON COLUMN anomaly_flood_parameters.pump_total_capacity_lpm IS
    '생성 컬럼: pump_count × pump_capacity_lpm. L4(배수불능) 판정 기준';
COMMENT ON COLUMN anomaly_flood_parameters.drain_disabled_margin_pct IS
    '배수불능 판정 안전 마진(%). 유입량 > 배수량*(1+margin/100) 시 L4 발령';
COMMENT ON COLUMN anomaly_flood_parameters.is_verified IS
    'FALSE: 기본값 또는 미확인 현장. TRUE: 현장 실측으로 검증 완료';


CREATE INDEX idx_flood_param_zone ON anomaly_flood_parameters (resource_id);


-- ════════════════════════════════════════════════════════════
-- 핵심 임계값 테이블 (화재/구조)
--    결로는 anomaly_thresholds 테이블을 별도 사용
-- ════════════════════════════════════════════════════════════
CREATE TABLE anomaly_thresholds (
    threshold_id    UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_id     VARCHAR(8)      NOT NULL,
    sensor_category  VARCHAR(100)    NOT NULL,
    sensor_element_type  VARCHAR(10)    NOT NULL,
    alert_level     alert_level      NOT NULL,
    operator        threshold_op     NOT NULL,
    threshold_value NUMERIC(12,4)    NOT NULL,
    aggregation     aggregation_fn   NOT NULL DEFAULT 'LAST',
    eval_window_sec INTEGER          NOT NULL DEFAULT 60,   --"몇 초 동안의 데이터를 기준으로 판단할 것인가?"
    composite_group VARCHAR(30),                            --"센서 여러 개 중 몇 개 이상이 동시에 임계 초과하면 경보"
    composite_min_count SMALLINT     DEFAULT 1,
    composite_target_level alert_level,
    description     TEXT,
    is_active       BOOLEAN          NOT NULL DEFAULT TRUE,
    created_by      VARCHAR(60),
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_active_threshold
        UNIQUE (resource_id, sensor_category, sensor_element_type, alert_level)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX idx_anomaly_thresholds_sensor  ON anomaly_thresholds (sensor_category, sensor_element_type);

-- ════════════════════════════════════════════════════════════
-- 기본 임계값 데이터 삽입
-- ════════════════════════════════════════════════════════════
   INSERT INTO anomaly_thresholds
        (resource_id, sensor_category, sensor_element_type, alert_level, operator, threshold_value,
         aggregation, eval_window_sec, description)
    VALUES
        -- ── 화재 ──────────────────────────────────────
        ('R0000001','SC000012','SE000021',  'LEVEL_1','GTE', 8,    'DERIVATIVE',60, '분당 온도 상승 8℃/min 이상'), -- 내부온습도센서/온도 
        ('R0000001','SC000012', 'SE000021', 'LEVEL_2','GTE', 60,   'LAST',      60, '절대온도 60℃ 이상'),
        ('R0000001','SC000012', 'SE000021', 'LEVEL_3','GTE', 75,   'LAST',      60, '절대온도 75℃ 이상'),
        ('R0000001','SC000009', 'SE000012',  'LEVEL_1','LTE', 15,   'LAST',      60, 'O₂ 15% 이하'), -- 복합가스센서/ O2
        ('R0000001','SC000009', 'SE000012',  'LEVEL_2','LTE', 10,   'LAST',      60, 'O₂ 10% 이하'), -- 복합가스센서/ O2
        ('R0000001','SC000009',  'SE000012', 'LEVEL_3','LTE', 8,    'LAST',      60, 'O₂ 8% 이하'), -- 복합가스센서/ O2
        ('R0000001','SC000009', 'SE000013',  'LEVEL_1','GTE', 1400, 'LAST',      60, 'CO 1,400ppm 이상'), --복합가스센서/CO
        ('R0000001','SC000009', 'SE000013',   'LEVEL_2','GTE', 2000, 'LAST',      60, 'CO 2,000ppm 이상'), --복합가스센서/CO
        ('R0000001','SC000009', 'SE000013',   'LEVEL_3','GTE', 2500, 'LAST',      60, 'CO 2,500ppm 이상'), --복합가스센서/CO
        ('R0000001','SC000009', 'SE000014',   'LEVEL_1','GTE', 5,    'LAST',      60, 'CO₂ 5% 이상'), -- 복합가스센서 /	CO2
        ('R0000001','SC000009', 'SE000014',   'LEVEL_2','GTE', 10,   'LAST',      60, 'CO₂ 10% 이상'), -- 복합가스센서 /	CO2
        ('R0000001','SC000009', 'SE000014',   'LEVEL_3','GTE', 30,   'LAST',      60, 'CO₂ 30% 이상'), -- 복합가스센서 /	CO2
        ('R0000001','SC000009', 'SE000015',   'LEVEL_1','GTE', 100,  'LAST',      60, 'H₂S 100ppm 이상'), --복합가스센서	/	H2S
        ('R0000001','SC000009', 'SE000015',   'LEVEL_2','GTE', 200,  'LAST',      60, 'H₂S 200ppm 이상'), --복합가스센서	/	H2S
        ('R0000001','SC000009', 'SE000015',   'LEVEL_3','GTE', 500,  'LAST',      60, 'H₂S 500ppm 이상'), --복합가스센서	/	H2S
         -- ── 구조 ───────────────────────────────────────────
        ('R0000001','SC000001', 'SE000001',  'LEVEL_1','GTE', 0.1,  'MEAN',      300,'균열폭 0.1mm 이상'), --균열센서/	균열
        ('R0000001','SC000001', 'SE000001',  'LEVEL_2','GTE', 0.3,  'MEAN',      300,'균열폭 0.3mm 이상'), --균열센서/	균열
        ('R0000001','SC000001', 'SE000001',  'LEVEL_3','GTE', 0.5,  'MEAN',      300,'균열폭 0.5mm 이상'), --균열센서/	균열
        ('R0000001','SC000003', 'SE000004',  'LEVEL_1','GT',  2000, 'MEAN',      300,'변형률 2,000με 초과'), --	변형률센서	/	변형률
        ('R0000001','SC000003', 'SE000004',  'LEVEL_2','GT',  2500, 'MEAN',      300,'변형률 2,500με 초과'),
        ('R0000001','SC000003', 'SE000004',  'LEVEL_3','GT',  3000, 'MEAN',      300,'변형률 3,000με 초과'),
        ('R0000001','SC000005', 'SE000006',   'LEVEL_1','GTE', 0.2,  'MAX',       300,'진동가속도 0.2cm/s 이상'), --진동가속도계/진동가속도X
        ('R0000001','SC000005', 'SE000006',   'LEVEL_2','GTE', 0.5,  'MAX',       300,'진동가속도 0.5cm/s 이상'), --진동가속도계/진동가속도X
        ('R0000001','SC000005', 'SE000006',   'LEVEL_3','GTE', 1.0,  'MAX',       300,'진동가속도 1.0cm/s 이상'); --진동가속도계/진동가속도X
