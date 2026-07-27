from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ANALYZE_MESSAGE = "\uc774 \uc5c5\ubb34\uc815\uc758\uc11c\ub97c \ubd84\uc11d\ud574\uc11c \uc790\ub3d9\ud654 \ud750\ub984\ub3c4\uae4c\uc9c0 \ub9cc\ub4e4\uc5b4\uc918"
RECOMMEND_MESSAGE = "\uc774 \uc5c5\ubb34\uc815\uc758\uc11c\ub85c \uc790\ub3d9\ud654 \ud750\ub984\ub3c4 \ub9cc\ub4e4\uc5b4\uc918"


@dataclass
class StepLog:
    name: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | None = None
    events_log: str | None = None
    error: str | None = None


class RunnerError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_goldset_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "eval_inputs").exists() and (parent / "runner").exists():
            return parent
    raise RunnerError(f"Could not locate scripts/goldset root from {current}")


def default_eval_input(goldset_root: Path) -> Path:
    pdfs = sorted((goldset_root / "eval_inputs" / "pdfs").glob("*.pdf"))
    if not pdfs:
        raise RunnerError(f"No eval input PDFs found under {goldset_root / 'eval_inputs' / 'pdfs'}")
    return pdfs[0]


def parse_args() -> argparse.Namespace:
    goldset_root = find_goldset_root()
    parser = argparse.ArgumentParser(
        description="Run the frontend-equivalent v2 PDF upload -> parse -> analyze -> recommend flow."
    )
    parser.add_argument("--input", type=Path, default=default_eval_input(goldset_root), help="PDF eval input path.")
    parser.add_argument("--base-url", default=os.getenv("A360_BACKEND_URL", "http://localhost:8000"))
    parser.add_argument("--agent-version", default="v2")
    parser.add_argument("--token", default=os.getenv("A360_ACCESS_TOKEN"), help="Bearer token if auth is required.")
    parser.add_argument("--run-id", default=f"runner_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--no-recommend", action="store_true", help="Stop after analysis.")
    return parser.parse_args()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def request_headers(token: str | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def read_error_body(err: HTTPError) -> str:
    try:
        return err.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def http_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    body = None
    headers = request_headers(token)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else None
            return {"status": resp.status, "headers": dict(resp.headers.items()), "data": data}
    except HTTPError as err:
        raise RunnerError(f"{method} {path} failed: HTTP {err.code} {read_error_body(err)}") from err
    except URLError as err:
        raise RunnerError(f"{method} {path} failed: {err.reason}") from err


def multipart_upload(
    base_url: str,
    path: str,
    *,
    file_path: Path,
    token: str | None,
    timeout: float,
    session_id: str | None = None,
) -> dict[str, Any]:
    boundary = f"----runner-v2-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")

    if session_id:
        add_field("session_id", session_id)

    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    headers = request_headers(token, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    req = Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "headers": dict(resp.headers.items()), "data": json.loads(raw)}
    except HTTPError as err:
        raise RunnerError(f"POST {path} failed: HTTP {err.code} {read_error_body(err)}") from err
    except URLError as err:
        raise RunnerError(f"POST {path} failed: {err.reason}") from err


def parse_sse_frame(frame: str) -> dict[str, Any] | None:
    data_lines = []
    for line in frame.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return None
    try:
        return json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return {"event": "malformed", "raw": "\n".join(data_lines)}


def http_sse(
    base_url: str,
    path: str,
    *,
    method: str = "POST",
    token: str | None,
    payload: dict[str, Any] | None = None,
    timeout: float,
    events_log: Path,
) -> dict[str, Any]:
    body = None
    headers = request_headers(token)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(f"{base_url.rstrip('/')}{path}", data=body or b"", headers=headers, method=method)
    final_event: dict[str, Any] | None = None
    counts: dict[str, int] = {}
    started = time.perf_counter()

    try:
        with urlopen(req, timeout=timeout) as resp:
            frame_lines: list[str] = []

            def record_event(event: dict[str, Any]) -> dict[str, Any] | None:
                nonlocal final_event
                event_name = event.get("event", "unknown")
                counts[event_name] = counts.get(event_name, 0) + 1
                append_jsonl(events_log, {"received_at": utc_now(), **event})
                if event_name in {"done", "error"}:
                    final_event = event
                    return {
                        "status": resp.status,
                        "headers": dict(resp.headers.items()),
                        "counts": counts,
                        "duration_seconds": round(time.perf_counter() - started, 3),
                        "final_event": final_event,
                    }
                return None

            while True:
                raw_line = resp.readline()
                if raw_line == b"":
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    frame_lines.append(line)
                    continue

                if frame_lines:
                    event = parse_sse_frame("\n".join(frame_lines))
                    frame_lines = []
                    if not event:
                        continue
                    result = record_event(event)
                    if result:
                        return result

            if frame_lines:
                event = parse_sse_frame("\n".join(frame_lines))
                if event:
                    result = record_event(event)
                    if result:
                        return result
            return {
                "status": resp.status,
                "headers": dict(resp.headers.items()),
                "counts": counts,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "final_event": final_event,
            }
    except HTTPError as err:
        raise RunnerError(f"{method} {path} failed: HTTP {err.code} {read_error_body(err)}") from err
    except URLError as err:
        raise RunnerError(f"{method} {path} failed: {err.reason}") from err


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RunnerError(message)


def require_sse_done(step: StepLog, manifest_path: Path, summary: dict[str, Any], steps: list[StepLog]) -> dict[str, Any]:
    final_event = step.response.get("final_event") if step.response else None
    if not final_event:
        step.status = "error"
        step.error = "SSE stream ended without a done/error event"
    elif final_event.get("event") == "error":
        step.status = "error"
        step.error = final_event.get("message") or "SSE stream returned error"
    elif final_event.get("event") != "done":
        step.status = "error"
        step.error = f"SSE stream ended with unexpected event: {final_event.get('event')}"
    if step.status == "error":
        write_json(manifest_path, {**summary, "status": "error", "steps": [asdict(s) for s in steps]})
        raise RunnerError(f"{step.name} failed: {step.error}")
    return final_event.get("data") or {}


def run_step(step: StepLog, fn) -> StepLog:
    try:
        step.response = fn()
        step.status = "ok"
    except Exception as exc:  # noqa: BLE001
        step.status = "error"
        step.error = str(exc)
    finally:
        step.finished_at = utc_now()
    return step


def main() -> int:
    args = parse_args()
    goldset_root = find_goldset_root()
    input_path = args.input.resolve()
    if not input_path.exists():
        raise RunnerError(f"Input file does not exist: {input_path}")

    log_dir = goldset_root / "runner" / "logs" / args.run_id
    manifest_path = log_dir / "run_manifest.json"
    log_dir.mkdir(parents=True, exist_ok=True)
    steps: list[StepLog] = []
    summary: dict[str, Any] = {
        "run_id": args.run_id,
        "created_at": utc_now(),
        "runner": "runner_v2",
        "base_url": args.base_url,
        "agent_version": args.agent_version,
        "input": str(input_path),
        "frontend_equivalent_flow": [
            "POST /api/documents",
            "POST /api/documents/{id}/parse",
            "POST /api/sessions/{session_id}/turn analyze",
            "POST /api/sessions/{session_id}/turn recommend",
        ],
    }

    def add_step(name: str, request: dict[str, Any], fn) -> StepLog:
        step = StepLog(name=name, started_at=utc_now(), request=request)
        run_step(step, fn)
        steps.append(step)
        write_json(manifest_path, {**summary, "steps": [asdict(s) for s in steps]})
        if step.status != "ok":
            raise RunnerError(f"{name} failed: {step.error}")
        return step

    upload = add_step(
        "uploadDocument",
        {"method": "POST", "path": "/api/documents", "file": str(input_path)},
        lambda: multipart_upload(
            args.base_url,
            "/api/documents",
            file_path=input_path,
            token=args.token,
            timeout=args.timeout,
        ),
    )
    upload_data = upload.response["data"] if upload.response else {}
    session_id = upload_data.get("session_id")
    document_id = upload_data.get("id")
    require(bool(session_id and document_id), "Upload response did not include session_id and document id")

    parse_events = log_dir / "02_parseDocument.events.jsonl"
    parse_step = StepLog(
        name="parseDocument",
        started_at=utc_now(),
        request={"method": "POST", "path": f"/api/documents/{document_id}/parse"},
        events_log=str(parse_events),
    )
    run_step(
        parse_step,
        lambda: http_sse(
            args.base_url,
            f"/api/documents/{document_id}/parse",
            token=args.token,
            timeout=args.timeout,
            events_log=parse_events,
        ),
    )
    steps.append(parse_step)
    write_json(manifest_path, {**summary, "steps": [asdict(s) for s in steps]})
    if parse_step.status != "ok":
        raise RunnerError(f"parseDocument failed: {parse_step.error}")
    parse_data = require_sse_done(parse_step, manifest_path, summary, steps)
    require(parse_data.get("status") == "parsed", "Parse done payload was not parsed")

    analyze_events = log_dir / "03_turn_analyze.events.jsonl"
    analyze_payload = {
        "message": ANALYZE_MESSAGE,
        "operation": "chat",
        "agent_version": args.agent_version,
    }
    analyze_step = StepLog(
        name="turnAnalyze",
        started_at=utc_now(),
        request={"method": "POST", "path": f"/api/sessions/{session_id}/turn", "json": analyze_payload},
        events_log=str(analyze_events),
    )
    run_step(
        analyze_step,
        lambda: http_sse(
            args.base_url,
            f"/api/sessions/{session_id}/turn",
            token=args.token,
            payload=analyze_payload,
            timeout=args.timeout,
            events_log=analyze_events,
        ),
    )
    steps.append(analyze_step)
    write_json(manifest_path, {**summary, "steps": [asdict(s) for s in steps]})
    if analyze_step.status != "ok":
        raise RunnerError(f"turnAnalyze failed: {analyze_step.error}")
    analyze_data = require_sse_done(analyze_step, manifest_path, summary, steps)
    require(bool(analyze_data.get("analysis_result")), "Analyze done payload did not include analysis_result")

    if not args.no_recommend:
        recommend_events = log_dir / "04_turn_recommend.events.jsonl"
        recommend_payload = {
            "message": RECOMMEND_MESSAGE,
            "operation": "chat",
            "agent_version": args.agent_version,
        }
        recommend_step = StepLog(
            name="turnRecommend",
            started_at=utc_now(),
            request={"method": "POST", "path": f"/api/sessions/{session_id}/turn", "json": recommend_payload},
            events_log=str(recommend_events),
        )
        run_step(
            recommend_step,
            lambda: http_sse(
                args.base_url,
                f"/api/sessions/{session_id}/turn",
                token=args.token,
                payload=recommend_payload,
                timeout=args.timeout,
                events_log=recommend_events,
            ),
        )
        steps.append(recommend_step)
        write_json(manifest_path, {**summary, "steps": [asdict(s) for s in steps]})
        if recommend_step.status != "ok":
            raise RunnerError(f"turnRecommend failed: {recommend_step.error}")
        recommend_data = require_sse_done(recommend_step, manifest_path, summary, steps)
        recommendation = recommend_data.get("recommendation") or {}
        require(bool(recommendation.get("steps")), "Recommend done payload did not include recommendation.steps")

    final_manifest = {
        **summary,
        "finished_at": utc_now(),
        "status": "ok",
        "session_id": session_id,
        "document_id": document_id,
        "steps": [asdict(s) for s in steps],
    }
    write_json(manifest_path, final_manifest)
    print(json.dumps({"status": "ok", "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RunnerError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
