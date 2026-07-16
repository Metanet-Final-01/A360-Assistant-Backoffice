# RAG Smoke Scripts

Small, reusable smoke tests for remote RAG storage and periodic ingest wiring.
They use dedicated test resources and do not write to the production
`rag_documents` table or `rag_documents` OpenSearch index.

## Remote Store CRUD

Creates `rag_documents_test` in PostgreSQL and `rag_documents_test` in
OpenSearch, then verifies create, update/upsert, delete, schema columns, and
OpenSearch mappings.

```powershell
cd "C:\Users\KDH\Documents\VisualStudio Code\A360-Assistant\A360-Assistant-Ops\rag-server"
.\.venv\Scripts\python.exe scripts\smoke\remote_test_rag_store.py
```

Default test resources:

```text
RAG_TEST_TABLE=rag_documents_test
RAG_TEST_INDEX=rag_documents_test
```

## Periodic Ingest Smoke Server

Runs a tiny local server with the same `/rag/ingest` contract as the real
rag-server. It writes one sentinel document to remote test resources, allowing
this path to be verified safely:

```text
EventBridge Scheduler -> SQS -> local consumer -> local smoke server -> remote test table/index
```

Start the smoke server:

```powershell
cd "C:\Users\KDH\Documents\VisualStudio Code\A360-Assistant\A360-Assistant-Ops\rag-server"
.\.venv\Scripts\python.exe -m uvicorn scripts.smoke.periodic_ingest_smoke_server:app --host 127.0.0.1 --port 8201
```

Run the SQS consumer against the smoke server:

```powershell
cd "C:\Users\KDH\Documents\VisualStudio Code\A360-Assistant\A360-Assistant-Ops\ops-server\backend"
$env:AWS_PROFILE="a360-admin"
$env:AWS_REGION="ap-northeast-2"
python -m app.scheduler.sqs_consumer `
  --queue-url "https://sqs.ap-northeast-2.amazonaws.com/533267199297/a360-rag-ingest-dev-queue" `
  --rag-server-url "http://127.0.0.1:8201" `
  --once
```

Default test resources:

```text
RAG_PERIODIC_TEST_TABLE=rag_documents_periodic_test
RAG_PERIODIC_TEST_INDEX=rag_documents_periodic_test
```

## Cleanup

Drop periodic smoke resources:

```sql
DROP TABLE IF EXISTS rag_documents_periodic_test;
```

OpenSearch:

```http
DELETE /rag_documents_periodic_test
```

Drop CRUD smoke resources:

```sql
DROP TABLE IF EXISTS rag_documents_test;
```

OpenSearch:

```http
DELETE /rag_documents_test
```
