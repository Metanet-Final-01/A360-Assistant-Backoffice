# Streamlit UI (:8501) — Ops Backend만 호출, DB에 직접 붙지 않는다 (RPA-259)
#
# ⚠️ 이 컨테이너에는 DB 자격증명을 주입하지 않는다. UI는 OPS_BACKEND_URL(HTTP)로만 통신한다.
# frontend/requirements.txt에 psycopg 등 DB 드라이버가 없음을 확인했다(계약: UI는 DB 미접근).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY ops-server/frontend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && (curl --version >/dev/null 2>&1 || (apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*))

COPY ops-server/frontend/ ./
RUN chown -R 1000:1000 /app
USER 1000:1000

EXPOSE 8501
# Streamlit 기본 헬스 엔드포인트
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

# headless·CORS off(내부 ALB 뒤). SIGTERM에 종료.
CMD ["python", "-m", "streamlit", "run", "app.py", \
     "--server.port", "8501", "--server.address", "0.0.0.0", \
     "--server.headless", "true", "--browser.gatherUsageStats", "false"]
