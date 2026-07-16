from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_RAG_SERVER_URL = "http://127.0.0.1:8200"
DEFAULT_STATUS_TIMEOUT_SECONDS = 3600.0
DEFAULT_STATUS_POLL_SECONDS = 10.0
DEFAULT_MESSAGE_VISIBILITY_SECONDS = 300


@dataclass(frozen=True)
class RagIngestMessage:
    option: int = 3
    clean: bool = False
    schedule_id: str = ""

    @classmethod
    def from_body(cls, body: str) -> "RagIngestMessage":
        payload = json.loads(body)
        if payload.get("type") not in {None, "rag_ingest"}:
            raise ValueError(f"Unsupported SQS message type: {payload.get('type')}")
        option = payload.get("option", 3)
        clean = payload.get("clean", False)
        if isinstance(option, bool) or not isinstance(option, int) or option not in {1, 2, 3}:
            raise ValueError("option must be an integer in the range 1..3")
        if not isinstance(clean, bool):
            raise ValueError("clean must be a boolean")
        return cls(
            option=option,
            clean=clean,
            schedule_id=str(payload.get("schedule_id") or ""),
        )


class SqsRagIngestConsumer:
    """Poll SQS and invoke localhost rag-server for each ingest message."""

    def __init__(
        self,
        *,
        queue_url: str,
        rag_server_url: str | None = None,
        region_name: str | None = None,
        sqs_client: Any | None = None,
        http_client: httpx.Client | None = None,
        status_timeout_seconds: float = DEFAULT_STATUS_TIMEOUT_SECONDS,
        status_poll_seconds: float = DEFAULT_STATUS_POLL_SECONDS,
        message_visibility_seconds: int = DEFAULT_MESSAGE_VISIBILITY_SECONDS,
    ):
        self.queue_url = queue_url
        self.rag_server_url = (rag_server_url or os.getenv("RAG_SERVER_URL") or DEFAULT_RAG_SERVER_URL).rstrip("/")
        self.region_name = region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2"
        self.sqs_client = sqs_client or self._make_sqs_client()
        self.http_client = http_client or httpx.Client(timeout=30.0)
        self.status_timeout_seconds = status_timeout_seconds
        self.status_poll_seconds = status_poll_seconds
        self.message_visibility_seconds = message_visibility_seconds

    def _make_sqs_client(self):
        import boto3

        return boto3.client("sqs", region_name=self.region_name)

    def send_test_message(self, *, option: int = 3, clean: bool = False, schedule_id: str = "manual-test") -> dict:
        body = json.dumps(
            {"type": "rag_ingest", "schedule_id": schedule_id, "option": option, "clean": clean},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return self.sqs_client.send_message(QueueUrl=self.queue_url, MessageBody=body)

    def handle_message(self, message: dict) -> dict:
        ingest_message = RagIngestMessage.from_body(message["Body"])
        rag_response = self.http_client.post(
            f"{self.rag_server_url}/rag/ingest",
            params={"option": ingest_message.option, "clean": ingest_message.clean},
        )
        rag_response.raise_for_status()
        rag_body = response_body(rag_response)
        run_id = rag_body.get("run_id") if isinstance(rag_body, dict) else None
        if run_id:
            self.wait_for_successful_run(str(run_id), receipt_handle=message["ReceiptHandle"])
        self.sqs_client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"])
        return {
            "status": "processed",
            "message_id": message.get("MessageId"),
            "schedule_id": ingest_message.schedule_id,
            "rag_response": rag_body,
        }

    def wait_for_successful_run(self, run_id: str, *, receipt_handle: str | None = None) -> dict:
        deadline = time.monotonic() + self.status_timeout_seconds
        while True:
            status_response = self.http_client.get(f"{self.rag_server_url}/rag/ingest/status")
            status_response.raise_for_status()
            status_body = response_body(status_response)
            if not isinstance(status_body, dict):
                raise RuntimeError("Unexpected RAG ingest status response")
            if status_body.get("run_id") == run_id and status_body.get("running") is False:
                if status_body.get("returncode") == 0:
                    return status_body
                raise RuntimeError(f"RAG ingest failed for run_id={run_id}: {status_body.get('error')}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for RAG ingest run_id={run_id}")
            if receipt_handle:
                self.sqs_client.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=receipt_handle,
                    VisibilityTimeout=self.message_visibility_seconds,
                )
            time.sleep(self.status_poll_seconds)

    def poll_once(self, *, wait_time_seconds: int = 10, max_number_of_messages: int = 1) -> list[dict]:
        response = self.sqs_client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_number_of_messages,
            WaitTimeSeconds=wait_time_seconds,
            VisibilityTimeout=300,
        )
        results = []
        for message in response.get("Messages", []):
            try:
                results.append(self.handle_message(message))
            except Exception as exc:
                results.append({
                    "status": "failed",
                    "message_id": message.get("MessageId"),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return results

    def run_forever(self, *, wait_time_seconds: int = 10, idle_sleep_seconds: float = 1.0) -> None:
        while True:
            results = self.poll_once(wait_time_seconds=wait_time_seconds)
            if not results:
                time.sleep(idle_sleep_seconds)


def response_body(response: httpx.Response) -> dict | str:
    try:
        return response.json()
    except ValueError:
        return response.text


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll SQS and trigger localhost rag-server ingest.")
    parser.add_argument("--queue-url", default=os.getenv("RAG_INGEST_SQS_QUEUE_URL"), required=False)
    parser.add_argument("--rag-server-url", default=os.getenv("RAG_SERVER_URL") or DEFAULT_RAG_SERVER_URL)
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--option", type=int, default=int(os.getenv("RAG_INGEST_OPTION", "3")))
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if not args.queue_url:
        raise SystemExit("Set --queue-url or RAG_INGEST_SQS_QUEUE_URL")

    consumer = SqsRagIngestConsumer(queue_url=args.queue_url, rag_server_url=args.rag_server_url)
    if args.send_test:
        print(json.dumps(consumer.send_test_message(option=args.option, clean=args.clean), default=str))
        return
    if args.once:
        print(json.dumps(consumer.poll_once(), ensure_ascii=False, default=str))
        return
    consumer.run_forever()


if __name__ == "__main__":
    main()
