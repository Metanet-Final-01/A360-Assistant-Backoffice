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
