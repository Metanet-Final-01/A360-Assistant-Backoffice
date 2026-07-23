-- 로컬 개발용 논리 DB 분리 (RPA-259 / RDS 3분리 계약)
-- postgres 컨테이너 최초 기동 시 1회 실행된다(docker-entrypoint-initdb.d).
--
-- 배포에서는 RDS 3대로 물리 분리되지만, 로컬은 컨테이너 1대 안에 논리 DB 3개로 나눈다.
-- 목적은 "폴백 제거 후에도 로컬이 도는지"를 배포와 같은 3-URL 구조로 검증하는 것 —
-- OBSERVABILITY_DATABASE_URL / RAG_DATABASE_URL을 명시 주입해 조용한 폴백을 재현 불가하게 한다.
--
-- 기본 DB(a360)는 POSTGRES_DB로 이미 생성되므로 나머지 둘만 만든다.
CREATE DATABASE a360_obs;
CREATE DATABASE a360_rag;
