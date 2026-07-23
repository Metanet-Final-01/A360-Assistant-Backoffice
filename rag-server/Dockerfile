# rag-server (FastAPI :8200) — A360 문서 수집→빌드→rag_documents/OpenSearch 적재 (RPA-259)
#
# 배포에서 RAG RDS 적재는 이 컨테이너만 한다. `RAG_DATABASE_URL`에는 `rag_ingest` Secret을
# 주입한다(§4.3 — Backend의 rag_runtime과 분리). RDS 마스터 Secret은 여기 주입하지 않는다.
#
# 적재는 sys.executable로 app/rag/scripts/run_option*.py를 subprocess 실행하므로 전체 코드 복사.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY rag-server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && (curl --version >/dev/null 2>&1 || (apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*))

COPY rag-server/ ./

# 적재 로그(app/rag/logs)·산출물(data)이 쓰는 경로 — read-only fs여도 여기만 쓰기 가능하게 볼륨.
RUN mkdir -p /app/data /app/app/rag/logs && chown -R 1000:1000 /app
VOLUME ["/app/data", "/app/app/rag/logs"]

USER 1000:1000

EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8200/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
