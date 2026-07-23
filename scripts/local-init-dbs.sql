-- 로컬 개발용 논리 DB 분리 (RPA-259 / RDS 3분리 계약)
-- postgres 컨테이너가 "빈 pgdata 볼륨으로 최초 기동"할 때 1회 실행된다(docker-entrypoint-initdb.d).
--
-- ⚠️ 이 파일은 데이터 디렉터리가 비어 있을 때만 자동 실행된다. 이미 만들어진 pgdata 볼륨
--    (이 PR 이전 구성 등)에는 a360_obs/a360_rag가 없을 수 있고, 그때는 이 스크립트가 다시
--    돌지 않는다 → 서비스가 설정된 DSN으로 연결에 실패한다. 반영하려면 볼륨을 지우고 다시 띄운다:
--        docker compose down -v && docker compose up -d
--    (아래 멱등화는 "스크립트가 도는 경우"의 재실행 안전만 보장할 뿐, 안 도는 이 경우는 못 고친다.)
--
-- 배포에서는 RDS 3대로 물리 분리되지만, 로컬은 컨테이너 1대 안에 논리 DB 3개로 나눈다.
-- 목적은 "폴백 제거 후에도 로컬이 도는지"를 배포와 같은 3-URL 구조로 검증하는 것 —
-- OBSERVABILITY_DATABASE_URL / RAG_DATABASE_URL을 명시 주입해 조용한 폴백을 재현 불가하게 한다.
--
-- CREATE DATABASE는 이미 있으면 에러다(수동 재실행·부분 초기화 시). \gexec로 "없을 때만 생성"해
-- 멱등화한다 — SELECT가 행을 안 내면 \gexec는 아무것도 실행하지 않는다.
-- 기본 DB(a360)는 POSTGRES_DB로 이미 생성되므로 나머지 둘만 만든다.
SELECT 'CREATE DATABASE a360_obs' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'a360_obs')\gexec
SELECT 'CREATE DATABASE a360_rag' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'a360_rag')\gexec

\connect a360_obs

CREATE TABLE IF NOT EXISTS audit_logs (
    id bigserial PRIMARY KEY,
    request_id text,
    user_id uuid,
    method text,
    path text,
    status_code integer,
    latency_ms double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created_id ON audit_logs (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id ON audit_logs (request_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs (user_id);

CREATE TABLE IF NOT EXISTS request_metrics (
    id bigserial PRIMARY KEY,
    request_id text,
    user_id uuid,
    method text,
    path text,
    status_code integer,
    latency_ms double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_request_metrics_created_id ON request_metrics (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_request_metrics_request_id ON request_metrics (request_id);
CREATE INDEX IF NOT EXISTS idx_request_metrics_user_id ON request_metrics (user_id);
CREATE INDEX IF NOT EXISTS idx_request_metrics_method_path ON request_metrics (method, path);

CREATE TABLE IF NOT EXISTS rag_events (
    id bigserial PRIMARY KEY,
    request_id text,
    event text,
    function text,
    status text,
    duration_ms double precision,
    detail jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rag_events_created_id ON rag_events (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_rag_events_request_id ON rag_events (request_id);
CREATE INDEX IF NOT EXISTS idx_rag_events_event ON rag_events (event);

CREATE TABLE IF NOT EXISTS turn_events (
    id bigserial PRIMARY KEY,
    session_id uuid,
    request_id text,
    seq integer,
    kind text,
    stage text,
    message text,
    detail jsonb,
    elapsed_ms double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_turn_events_created_id ON turn_events (created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_turn_events_session_id ON turn_events (session_id);
CREATE INDEX IF NOT EXISTS idx_turn_events_request_id ON turn_events (request_id);

CREATE TABLE IF NOT EXISTS llm_usage (
    id bigserial PRIMARY KEY,
    request_id text,
    user_id uuid,
    session_id uuid,
    component text,
    purpose text,
    model text,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd numeric(12, 6) NOT NULL DEFAULT 0,
    latency_ms double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at ON llm_usage (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_usage_component ON llm_usage (component);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage (model);
CREATE INDEX IF NOT EXISTS idx_llm_usage_user_id ON llm_usage (user_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_session_id ON llm_usage (session_id);

CREATE TABLE IF NOT EXISTS metrics_daily (
    day date NOT NULL,
    method text,
    path text,
    calls integer NOT NULL DEFAULT 0,
    err_4xx integer NOT NULL DEFAULT 0,
    err_5xx integer NOT NULL DEFAULT 0,
    p50_ms double precision,
    p95_ms double precision,
    avg_ms double precision,
    max_ms double precision,
    PRIMARY KEY (day, method, path)
);

CREATE INDEX IF NOT EXISTS idx_metrics_daily_day_calls ON metrics_daily (day DESC, calls DESC);

CREATE TABLE IF NOT EXISTS usage_daily (
    day date NOT NULL,
    component text,
    purpose text,
    model text,
    calls integer NOT NULL DEFAULT 0,
    input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd numeric(12, 6) NOT NULL DEFAULT 0,
    PRIMARY KEY (day, component, purpose, model)
);

CREATE INDEX IF NOT EXISTS idx_usage_daily_day ON usage_daily (day DESC);

\connect a360_rag

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
    id text PRIMARY KEY,
    source_type text NOT NULL,
    package_name text,
    action_name text,
    locale text,
    title text NOT NULL,
    url text,
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    embedding vector(1024),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS parent_id text;
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS chunk_index integer NOT NULL DEFAULT 0;
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS content_hash text;

CREATE INDEX IF NOT EXISTS idx_rag_documents_package ON rag_documents (package_name);
CREATE INDEX IF NOT EXISTS idx_rag_documents_source ON rag_documents (source_type);
CREATE INDEX IF NOT EXISTS idx_rag_documents_parent ON rag_documents (parent_id);
