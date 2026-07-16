"""RAG ingest server entrypoint.

POST /rag/ingest starts the crawl/build/ingest pipeline in a background task.
The target DB/OpenSearch are shared with A360-Assistant-Backend, so successful
ingest is reflected in the live RAG search path.
"""

from fastapi import BackgroundTasks, FastAPI, HTTPException

from . import ingest_jobs

app = FastAPI(title="A360 RAG Ingest Server")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def root() -> dict:
    return {"message": "A360 RAG Ingest Server is running."}


@app.post("/rag/ingest")
def trigger_rag_ingest(option: int, background_tasks: BackgroundTasks, clean: bool = False) -> dict:
    """Run the RAG ingest pipeline.

    option=1: JAR-backed package/action schemas only.
    option=2: option 1 plus naive action candidates for packages without JARs.
    option=3: option 2 plus LLM parsing for packages without JARs.
    clean=false: upsert changed rows only.
    clean=true: clear rag_documents/OpenSearch first, then reload everything.
    """
    if option not in ingest_jobs.OPTION_SCRIPTS:
        raise HTTPException(status_code=400, detail="option must be one of 1, 2, 3")

    state = ingest_jobs.reserve_job(option, clean)
    if state is None:
        raise HTTPException(status_code=409, detail="RAG ingest is already running")

    background_tasks.add_task(ingest_jobs.run_reserved_job, state)
    return {
        "status": "started",
        "run_id": state["run_id"],
        "option": option,
        "clean": clean,
        "log_path": state["log_path"],
    }


@app.get("/rag/ingest/status")
def rag_ingest_status() -> dict:
    return ingest_jobs.status()
