import json
import logging
from pathlib import Path

import httpx
import yaml

from app.scheduler.eventbridge_sqs import EventBridgeSqsSchedulerProvider
from app.scheduler.eventbridge_ssm import EventBridgeSsmSchedulerProvider
from app.scheduler.local_provider import LocalSchedulerProvider
from app.scheduler.schema import RagIngestScheduleRequest
from app.scheduler.sqs_consumer import SqsRagIngestConsumer


class CfnLoader(yaml.SafeLoader):
    pass


def _cfn_multi_constructor(loader, _tag_prefix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor("!", _cfn_multi_constructor)


def _request() -> RagIngestScheduleRequest:
    return RagIngestScheduleRequest(
        schedule_id="a360-rag-ingest-daily",
        schedule_expression="cron(0 3 * * ? *)",
        option=3,
        clean=False,
        target_tag_key="Role",
        target_tag_value="rag-ingest-server",
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


def test_ops_stack_defines_default_rag_ingest_schedule():
    template_path = Path(__file__).resolve().parents[3] / "infra" / "cloudformation" / "ops-stack.yml"
    template = yaml.load(template_path.read_text(encoding="utf-8"), Loader=CfnLoader)

    schedule = template["Resources"]["DefaultRagIngestSchedule"]
    target_input = schedule["Properties"]["Target"]["Input"]

    assert schedule["Condition"] == "CreatesDefaultRagIngestSchedule"
    assert template["Parameters"]["EnableDefaultRagIngestSchedule"]["Default"] == "true"
    assert template["Parameters"]["RagIngestScheduleExpression"]["Default"] == "cron(30 8 ? * * *)"
    assert template["Parameters"]["RagIngestScheduleTimezone"]["Default"] == "Asia/Seoul"
    assert '"type": "rag_ingest"' in target_input
    assert '"schedule_id": "${ProjectName}-${Environment}-rag-ingest-daily"' in target_input
    assert '"option": 3' in target_input
    assert '"clean": ${RagIngestClean}' in target_input


def test_sqs_consumer_processes_message_and_deletes_it():
    class FakeSqs:
        def __init__(self):
            self.deleted = []
            self.visibility_changes = []

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

        def change_message_visibility(self, **kwargs):
            self.visibility_changes.append(kwargs)

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-rag-token"
        if request.method == "POST":
            assert request.url.path == "/rag/ingest/jobs"
            assert json.loads(request.content) == {
                "mode": "extended",
                "clean": True,
                "requested_by": "eventbridge-sqs",
            }
            return httpx.Response(200, json={"job_id": "job-1", "status": "QUEUED"})
        assert request.method == "GET"
        assert request.url.path == "/rag/ingest/jobs/job-1"
        return httpx.Response(200, json={"job_id": "job-1", "status": "SUCCEEDED"})

    sqs = FakeSqs()
    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        rag_server_url="http://127.0.0.1:8200",
        sqs_client=sqs,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        service_token="test-rag-token",
    )

    result = consumer.poll_once(wait_time_seconds=0)

    assert result[0]["status"] == "processed"
    assert result[0]["rag_response"] == {"job_id": "job-1", "status": "QUEUED"}
    assert result[0]["rag_job"] == {"job_id": "job-1", "status": "SUCCEEDED"}
    assert [call.url.path for call in calls] == ["/rag/ingest/jobs", "/rag/ingest/jobs/job-1"]
    assert sqs.deleted == [{
        "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        "ReceiptHandle": "rh-1",
    }]


def test_sqs_consumer_keeps_message_when_rag_job_fails():
    class FakeSqs:
        def __init__(self):
            self.deleted = []

        def receive_message(self, **kwargs):
            return {
                "Messages": [{
                    "MessageId": "m-1",
                    "ReceiptHandle": "rh-1",
                    "Body": json.dumps({"type": "rag_ingest", "schedule_id": "test", "option": 3, "clean": False}),
                }]
            }

        def delete_message(self, **kwargs):
            self.deleted.append(kwargs)

        def change_message_visibility(self, **kwargs):
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"job_id": "job-fail", "status": "QUEUED"})
        return httpx.Response(200, json={"job_id": "job-fail", "status": "FAILED", "error_message": "boom"})

    sqs = FakeSqs()
    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        rag_server_url="http://127.0.0.1:8200",
        sqs_client=sqs,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        status_poll_seconds=0,
    )

    result = consumer.poll_once(wait_time_seconds=0)

    assert result[0]["status"] == "failed"
    assert "RAG ingest failed for job_id=job-fail" in result[0]["error"]
    assert sqs.deleted == []


def test_sqs_consumer_deletes_message_when_rag_job_is_skipped():
    class FakeSqs:
        def __init__(self):
            self.deleted = []

        def receive_message(self, **kwargs):
            return {
                "Messages": [{
                    "MessageId": "m-1",
                    "ReceiptHandle": "rh-1",
                    "Body": json.dumps({"type": "rag_ingest", "schedule_id": "test", "option": 3, "clean": False}),
                }]
            }

        def delete_message(self, **kwargs):
            self.deleted.append(kwargs)

        def change_message_visibility(self, **kwargs):
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"job_id": "job-skip", "status": "SKIPPED"})
        return httpx.Response(200, json={
            "job_id": "job-skip",
            "status": "SKIPPED",
            "error_message": "No restorable artifact",
        })

    sqs = FakeSqs()
    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        rag_server_url="http://127.0.0.1:8200",
        sqs_client=sqs,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        status_poll_seconds=0,
    )

    result = consumer.poll_once(wait_time_seconds=0)

    assert result[0]["status"] == "processed"
    assert result[0]["rag_job"]["status"] == "SKIPPED"
    assert sqs.deleted == [{
        "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        "ReceiptHandle": "rh-1",
    }]


def test_sqs_consumer_waits_for_existing_job_on_conflict_then_deletes_message():
    class FakeSqs:
        def __init__(self):
            self.deleted = []
            self.visibility_changes = []

        def receive_message(self, **kwargs):
            return {
                "Messages": [{
                    "MessageId": "m-1",
                    "ReceiptHandle": "rh-1",
                    "Body": json.dumps({"type": "rag_ingest", "schedule_id": "test", "option": 3, "clean": False}),
                }]
            }

        def delete_message(self, **kwargs):
            self.deleted.append(kwargs)

        def change_message_visibility(self, **kwargs):
            self.visibility_changes.append(kwargs)

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                409,
                json={"detail": {"message": "A RAG ingest job is already running.", "job_id": "job-existing"}},
            )
        return httpx.Response(200, json={"job_id": "job-existing", "status": "SUCCEEDED"})

    sqs = FakeSqs()
    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        rag_server_url="http://127.0.0.1:8200",
        sqs_client=sqs,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        status_poll_seconds=0,
    )

    result = consumer.poll_once(wait_time_seconds=0)

    assert result[0]["status"] == "processed_conflict"
    assert result[0]["rag_job"] == {"job_id": "job-existing", "status": "SUCCEEDED"}
    assert [call.url.path for call in calls] == ["/rag/ingest/jobs", "/rag/ingest/jobs/job-existing"]
    assert sqs.deleted == [{
        "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        "ReceiptHandle": "rh-1",
    }]


def test_sqs_consumer_extends_visibility_while_rag_job_is_running():
    class FakeSqs:
        def __init__(self):
            self.visibility_changes = []

        def delete_message(self, **kwargs):
            pass

        def change_message_visibility(self, **kwargs):
            self.visibility_changes.append(kwargs)

    statuses = iter([
        {"job_id": "job-1", "status": "RUNNING"},
        {"job_id": "job-1", "status": "SUCCEEDED"},
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(statuses))

    sqs = FakeSqs()
    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        sqs_client=sqs,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        status_poll_seconds=0,
        message_visibility_seconds=900,
    )

    assert consumer.wait_for_successful_job("job-1", receipt_handle="rh-1") == {
        "job_id": "job-1",
        "status": "SUCCEEDED",
    }
    assert sqs.visibility_changes == [{
        "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        "ReceiptHandle": "rh-1",
        "VisibilityTimeout": 900,
    }]


def test_sqs_consumer_logs_worker_results(caplog):
    caplog.set_level(logging.INFO, logger="rag-ingest-worker")

    class FakeSqs:
        def __init__(self):
            self.calls = 0

        def receive_message(self, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise SystemExit
            return {"Messages": []}

    consumer = SqsRagIngestConsumer(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/a360-rag-ingest",
        sqs_client=FakeSqs(),
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
    )

    try:
        consumer.run_forever(idle_sleep_seconds=0)
    except SystemExit:
        pass

    assert "starting rag ingest SQS worker" in caplog.text
