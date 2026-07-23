# Ops Backend (FastAPI :8100) — 관측 RDS 직접 조회 + eval + 스케줄러 (RPA-259)
#
# 배포에서 관측 RDS `ops_reader` Secret은 이 컨테이너에만 주입한다. Streamlit UI에는 넣지 않는다.
FROM python:3.13-slim

# 로그는 파일이 아니라 stdout/stderr로 (컨테이너 관례) + .pyc 미생성
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성 먼저 — 레이어 캐시 (코드만 바뀌면 재설치 안 함)
COPY ops-server/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && curl --version >/dev/null 2>&1 || (apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*)

# 앱 코드
COPY ops-server/backend/ ./

# JSONL 사본(log_store)이 쓰는 경로 — read-only fs여도 여기만 쓰기 가능하게 볼륨/tmpfs로 마운트한다.
# (화면은 관측 RDS 직접 조회로 옮겼으나 collect 엔드포인트가 아직 여기 쓴다 — RPA-256 후속)
RUN mkdir -p /app/data && chown -R 1000:1000 /app
VOLUME ["/app/data"]

# non-root
USER 1000:1000

EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8100/health || exit 1

# graceful shutdown: uvicorn은 SIGTERM에 정상 종료. exec form이라 PID 1로 신호를 받는다.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
