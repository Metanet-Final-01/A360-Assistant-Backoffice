from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_RAG_SERVER_URL = "http://127.0.0.1:8200"
DEFAULT_STATUS_TIMEOUT_SECONDS = 3600.0
DEFAULT_STATUS_POLL_SECONDS = 10.0
DEFAULT_MESSAGE_VISIBILITY_SECONDS = 300
OPTION_TO_JOB_MODE = {
    1: "standard",
    2: "extended",
    3: "agent_parse",
}
SUCCESS_JOB_STATUS = "SUCCEEDED"
SKIPPED_JOB_STATUS = "SKIPPED"
FAILED_JOB_STATUSES = {"FAILED", "CANCELED", "INTERRUPTED"}

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rag-ingest-worker")


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
    """Poll SQS and invoke the RAG ingest server for each ingest message."""

    def __init__(
        self,
        *,
        queue_url: str,
        rag_server_url: str | None = None,
        region_name: str | None = None,
        sqs_client: Any | None = None,
        http_client: httpx.Client | None = None,
        service_token: str | None = None,
        status_timeout_seconds: float = DEFAULT_STATUS_TIMEOUT_SECONDS,
        status_poll_seconds: float = DEFAULT_STATUS_POLL_SECONDS,
        message_visibility_seconds: int = DEFAULT_MESSAGE_VISIBILITY_SECONDS,
    ):
        self.queue_url = queue_url
        self.rag_server_url = (rag_server_url or os.getenv("RAG_SERVER_URL") or DEFAULT_RAG_SERVER_URL).rstrip("/")
        self.region_name = region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2"
        self.sqs_client = sqs_client or self._make_sqs_client()
        self.http_client = http_client or httpx.Client(timeout=30.0)
        self.service_token = service_token if service_token is not None else os.getenv("RAG_SERVICE_TOKEN", "")
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
        logger.info(
            "received rag ingest message message_id=%s schedule_id=%s option=%s clean=%s",
            message.get("MessageId"),
            ingest_message.schedule_id,
            ingest_message.option,
            ingest_message.clean,
        )
        rag_response = self.http_client.post(
            f"{self.rag_server_url}/rag/ingest/jobs",
            json={
                "mode": OPTION_TO_JOB_MODE[ingest_message.option],
                "clean": ingest_message.clean,
                "requested_by": "eventbridge-sqs",
            },
            headers=self.auth_headers(),
        )
        try:
            rag_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            rag_body = response_body(exc.response)
            job_id = extract_job_id(rag_body)
            if not job_id:
                raise RuntimeError("RAG ingest conflict response did not include job_id") from exc
            logger.info(
                "rag ingest already running; waiting existing job message_id=%s job_id=%s",
                message.get("MessageId"),
                job_id,
            )
            rag_job = self.wait_for_successful_job(str(job_id), receipt_handle=message["ReceiptHandle"])
            self.sqs_client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"])
            logger.info("deleted rag ingest message after existing job success message_id=%s job_id=%s", message.get("MessageId"), job_id)
            return {
                "status": "processed_conflict",
                "message_id": message.get("MessageId"),
                "schedule_id": ingest_message.schedule_id,
                "rag_response": rag_body,
                "rag_job": rag_job,
            }
        rag_body = response_body(rag_response)
        job_id = extract_job_id(rag_body)
        if not job_id:
            raise RuntimeError("RAG ingest response did not include job_id")
        logger.info("created rag ingest job message_id=%s job_id=%s", message.get("MessageId"), job_id)
        rag_job = self.wait_for_successful_job(str(job_id), receipt_handle=message["ReceiptHandle"])
        self.sqs_client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"])
        logger.info("deleted rag ingest message after job success message_id=%s job_id=%s", message.get("MessageId"), job_id)
        return {
            "status": "processed",
            "message_id": message.get("MessageId"),
            "schedule_id": ingest_message.schedule_id,
            "rag_response": rag_body,
            "rag_job": rag_job,
        }

    def wait_for_successful_run(self, run_id: str, *, receipt_handle: str | None = None) -> dict:
        return self.wait_for_successful_job(run_id, receipt_handle=receipt_handle)

    def wait_for_successful_job(self, job_id: str, *, receipt_handle: str | None = None) -> dict:
        deadline = time.monotonic() + self.status_timeout_seconds
        while True:
            status_response = self.http_client.get(
                f"{self.rag_server_url}/rag/ingest/jobs/{job_id}",
                headers=self.auth_headers(),
            )
            status_response.raise_for_status()
            status_body = response_body(status_response)
            if not isinstance(status_body, dict):
                raise RuntimeError("Unexpected RAG ingest status response")
            status = str(status_body.get("status") or "").upper()
            if status == SUCCESS_JOB_STATUS:
                logger.info("rag ingest job succeeded job_id=%s", job_id)
                return status_body
            if status == SKIPPED_JOB_STATUS:
                logger.info("rag ingest job skipped job_id=%s reason=%s", job_id, status_body.get("error_message"))
                return status_body
            if status in FAILED_JOB_STATUSES:
                raise RuntimeError(
                    f"RAG ingest failed for job_id={job_id}: "
                    f"{status_body.get('error_message') or status_body.get('error') or status}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for RAG ingest job_id={job_id}")
            if receipt_handle:
                self.sqs_client.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=receipt_handle,
                    VisibilityTimeout=self.message_visibility_seconds,
                )
                logger.info("extended rag ingest message visibility job_id=%s visibility_timeout=%s", job_id, self.message_visibility_seconds)
            time.sleep(self.status_poll_seconds)

    def auth_headers(self) -> dict[str, str]:
        if not self.service_token:
            return {}
        return {"Authorization": f"Bearer {self.service_token}"}

    def poll_once(self, *, wait_time_seconds: int = 10, max_number_of_messages: int = 1) -> list[dict]:
        response = self.sqs_client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=max_number_of_messages,
            WaitTimeSeconds=wait_time_seconds,
            VisibilityTimeout=300,
        )
        messages = response.get("Messages", [])
        if messages:
            logger.info("received %s SQS message(s) from rag ingest queue", len(messages))
        results = []
        for message in messages:
            try:
                results.append(self.handle_message(message))
            except Exception as exc:
                logger.exception("failed to process rag ingest message message_id=%s", message.get("MessageId"))
                results.append({
                    "status": "failed",
                    "message_id": message.get("MessageId"),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return results

    def run_forever(self, *, wait_time_seconds: int = 10, idle_sleep_seconds: float = 1.0) -> None:
        logger.info("starting rag ingest SQS worker queue_url=%s rag_server_url=%s", self.queue_url, self.rag_server_url)
        while True:
            results = self.poll_once(wait_time_seconds=wait_time_seconds)
            for result in results:
                logger.info("rag ingest worker result %s", json.dumps(result, ensure_ascii=False, default=str))
            if not results:
                time.sleep(idle_sleep_seconds)


def response_body(response: httpx.Response) -> dict | str:
    try:
        return response.json()
    except ValueError:
        return response.text


def extract_job_id(body: dict | str) -> str | None:
    if not isinstance(body, dict):
        return None
    job_id = body.get("job_id")
    if job_id:
        return str(job_id)
    detail = body.get("detail")
    if isinstance(detail, dict) and detail.get("job_id"):
        return str(detail["job_id"])
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll SQS and trigger RAG ingest server jobs.")
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
