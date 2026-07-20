import json

import httpx

from app.scheduler.eventbridge_sqs import EventBridgeSqsSchedulerProvider
from app.scheduler.eventbridge_ssm import EventBridgeSsmSchedulerProvider
from app.scheduler.local_provider import LocalSchedulerProvider
from app.scheduler.schema import RagIngestScheduleRequest
from app.scheduler.sqs_consumer import SqsRagIngestConsumer


def _request() -> RagIngestScheduleRequest:
    return RagIngestScheduleRequest(
        schedule_id="a360-rag-ingest-daily",
        schedule_expression="cron(0 3 * * ? *)",
        option=3,
        clean=False,
        target_tag_key="Role",
        target_tag_value="rag-server",
    )


def test_eventbridge_ssm_builds_scheduler_payload(monkeypatch):
    provider = EventBridgeSsmSchedulerProvider(
        role_arn="arn:aws:iam::123456789012:role/a360-rag-scheduler",
        rag_ingest_url="http://127.0.0.1:8200/rag/ingest",
    )

    result = provider.upsert_schedule(_request(), dry_run=True)

    assert result.status == "dry_run"
    assert result.provider == "eventbridge-ssm"
    assert result.payload["Name"] == "a360-rag-ingest-daily"
    assert result.payload["ScheduleExpression"] == "cron(0 3 * * ? *)"
    assert result.payload["Target"]["Arn"] == "arn:aws:scheduler:::aws-sdk:ssm:sendCommand"
    assert "curl -fsS -X POST" in result.payload["Target"]["Input"]
    assert "option=3&clean=false" in result.payload["Target"]["Input"]
    assert "\"Targets\"" in result.payload["Target"]["Input"]


def test_eventbridge_ssm_can_target_instance_ids():
    req = _request().model_copy(update={"instance_ids": ["i-0123456789abcdef0"]})
    provider = EventBridgeSsmSchedulerProvider(role_arn="arn:aws:iam::123456789012:role/a360-rag-scheduler")

    payload = provider.build_ssm_send_command_input(req)

    assert payload["InstanceIds"] == ["i-0123456789abcdef0"]
    assert "Targets" not in payload


def test_local_provider_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scheduler.local_provider.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.scheduler.local_provider.SCHEDULES_JSON", tmp_path / "rag_ingest_schedules.json")

    result = LocalSchedulerProvider().upsert_schedule(_request(), dry_run=True)

    assert result.status == "dry_run"
    assert not (tmp_path / "rag_ingest_schedules.json").exists()


def test_eventbridge_sqs_builds_scheduler_payload():
    req = _request().model_copy(update={
        "schedule_expression": "rate(1 minute)",
        "sqs_queue_url": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        "sqs_queue_arn": "arn:aws:sqs:ap-northeast-2:123456789012:a360-rag-ingest",
    })
    provider = EventBridgeSqsSchedulerProvider(role_arn="arn:aws:iam::123456789012:role/a360-rag-scheduler")

    result = provider.upsert_schedule(req, dry_run=True)

    assert result.status == "dry_run"
    assert result.provider == "eventbridge-sqs"
    assert result.payload["ScheduleExpression"] == "rate(1 minute)"
    assert result.payload["Target"]["Arn"] == "arn:aws:sqs:ap-northeast-2:123456789012:a360-rag-ingest"
    assert json.loads(result.payload["Target"]["Input"]) == {
        "type": "rag_ingest",
        "schedule_id": "a360-rag-ingest-daily",
        "option": 3,
        "clean": False,
    }


def test_sqs_consumer_processes_message_and_deletes_it():
    class FakeSqs:
        def __init__(self):
            self.deleted = []

        def receive_message(self, **kwargs):
            return {
                "Messages": [{
                    "MessageId": "m-1",
                    "ReceiptHandle": "rh-1",
                    "Body": json.dumps({"type": "rag_ingest", "schedule_id": "test", "option": 2, "clean": True}),
                }]
            }

        def delete_message(self, **kwargs):
            self.deleted.append(kwargs)

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/rag/ingest"
        assert request.url.params["option"] == "2"
        assert request.url.params["clean"] == "true"
        return httpx.Response(200, json={"accepted": True})

    sqs = FakeSqs()
    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        rag_server_url="http://127.0.0.1:8200",
        sqs_client=sqs,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = consumer.poll_once(wait_time_seconds=0)

    assert result[0]["status"] == "processed"
    assert result[0]["rag_response"] == {"accepted": True}
    assert len(calls) == 1
    assert sqs.deleted == [{
        "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        "ReceiptHandle": "rh-1",
    }]
