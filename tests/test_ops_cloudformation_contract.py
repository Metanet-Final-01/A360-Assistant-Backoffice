import json
from collections import Counter
from pathlib import Path
import re
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def _ops_template_text() -> str:
    return (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")


def _cloudwatch_agent_config(template: str) -> dict:
    match = re.search(
        r"cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent\.json <<'CWEOF'\n"
        r"(?P<body>.*?)\n\s+CWEOF",
        template,
        flags=re.DOTALL,
    )
    assert match is not None
    return json.loads(textwrap.dedent(match.group("body")))


def _resource_block(template: str, logical_id: str) -> str:
    lines = template.splitlines()
    start = next((i for i, line in enumerate(lines) if line == f"  {logical_id}:"), None)
    assert start is not None, f"Missing resource block: {logical_id}"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z0-9]+:$", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def test_ops_userdata_avoids_al2023_curl_conflict_and_preserves_logs():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "dnf install -y docker awscli jq aws-cfn-bootstrap" in template
    assert "dnf install -y docker awscli jq curl aws-cfn-bootstrap" not in template
    assert "upload_bootstrap_logs()" in template
    assert "ops-bootstrap-logs/${AWS::StackName}/$INSTANCE_ID" in template
    assert "latest/api/token" in template


def test_ops_ghcr_login_keeps_token_fallback_for_direct_stack_deploys():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "GHCR_USERNAME='${GhcrUsername}'" in template
    assert "GHCR_USERNAME=token" in template
    assert 'docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin' in template


def test_ops_stack_uses_network_stack_ops_subnet_export():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "${ProjectName}-${Environment}-PrivateOpsSubnetIds" in template
    assert "PrivateBackofficeSubnetIds" not in template


def test_ops_runtime_secret_is_injected_into_backend_and_rag_server():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")
    params = (ROOT / "infra/cloudformation/parameters/ops-dev.json").read_text(encoding="utf-8")

    assert "OpsRuntimeSecretArn" in template
    assert "OpsRuntimeSecret:" in template
    assert "CreateOpsRuntimeSecret" in template
    assert "HasExternalOpsRuntimeSecret" in template
    assert "ReadOpsRuntimeSecret" in template
    assert "type == \"object\"" in template
    assert "secret_string" in template
    assert "Ops runtime secret values must not contain CR/LF" in template
    assert 'test("\\\\r|\\\\n")' in template
    assert "--env-file /opt/a360/runtime.env" in template
    assert "!If [HasExternalOpsRuntimeSecret, !Ref OpsRuntimeSecretArn, !Ref OpsRuntimeSecret]" in template
    ops_runtime_param = next(
        entry for entry in json.loads(params) if entry["ParameterKey"] == "OpsRuntimeSecretArn"
    )
    assert ops_runtime_param["ParameterValue"] == ""


def test_ops_stack_owns_ops_specific_secret_shells():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "OpsGhcrReadTokenSecret:" in template
    assert "RagServiceTokenSecret:" in template
    assert "CreateGhcrTokenSecret" in template
    assert "CreateRagServiceTokenSecret" in template
    assert "${ProjectName}/${Environment}/ops-ghcr-read-token" in template
    assert "SecretString: REPLACE_ME" in template
    assert '$(cat /opt/a360/ghcr_token)" != "REPLACE_ME"' in template
    assert "${ProjectName}/${Environment}/ops-runtime" in template
    assert "${ProjectName}/${Environment}/rag-service-token" in template
    assert "OpsGhcrReadTokenSecretArn:" in template
    assert "OpsRuntimeSecretArn:" in template
    assert "RagServiceTokenSecretArn:" in template


def test_rag_worker_log_group_uses_cloudformation_owned_name():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "RagWorkerLogGroup:" in template
    assert "/a360/${Environment}/rag-worker-cfn" in template
    assert 'awslogs-group=${RagWorkerLogGroupName}' in template


def test_ops_jsonl_files_are_tailed_to_cloudwatch_for_firehose_archive():
    template = _ops_template_text()
    config = _cloudwatch_agent_config(template)
    collect_list = config["logs"]["logs_collected"]["files"]["collect_list"]
    paths = [entry["file_path"] for entry in collect_list]
    duplicates = sorted(path for path, count in Counter(paths).items() if count > 1)
    assert not duplicates, f"Duplicate CloudWatch Agent file_path entries: {duplicates}"
    destinations = {entry["file_path"]: entry["log_group_name"] for entry in collect_list}

    assert '"logs_collected"' in template
    assert destinations["/opt/a360/rag-server-logs/*.jsonl"] == "${RagLogGroup}"
    assert destinations["/opt/a360/ops-backend-data/eval_runs.jsonl"] == "${OpsApiLogGroup}"
    assert destinations["/opt/a360/ops-backend-data/observability_*.jsonl"] == "${OpsApiLogGroup}"
    assert "-v /opt/a360/rag-server-logs:/app/app/rag/logs" in template
    assert "-v /opt/a360/ops-backend-data:/app/data" in template


def test_legacy_jsonl_log_groups_are_retained_if_removed_later():
    template = _ops_template_text()

    for logical_id in ("RagAopEventLogGroup", "OpsEvalLogGroup"):
        block = _resource_block(template, logical_id)
        assert "DeletionPolicy: Retain" in block
        assert "UpdateReplacePolicy: Retain" in block


def test_ops_ec2_direct_access_is_limited_to_client_vpn():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "AWS::ElasticLoadBalancingV2::LoadBalancer" not in template
    assert "AWS::ElasticLoadBalancingV2::TargetGroup" not in template
    assert "AWS::ElasticLoadBalancingV2::Listener" not in template
    assert "TargetGroupARNs" not in template
    assert "CidrIp: !Ref ClientVpnCidr" in template
    assert "OpsUiIngressFromClientVpnSecurityGroup" in template
    assert "OpsBackendIngressFromClientVpnSecurityGroup" in template
    assert "${ProjectName}-${Environment}-ClientVpnSecurityGroupId" in template
    assert "SourceSecurityGroupId:" in template
    assert "InternalAlbDnsName" not in template
    assert "InternalAlbSecurityGroupId" not in template


def test_ops_direct_access_has_route53_private_dns():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ops-deploy.yml").read_text(encoding="utf-8")

    assert "EnableOpsPrivateDns" in template
    assert "Default: dev.a360.internal" in template
    assert "Default: ops.dev.a360.internal" in template
    assert "OpsPrivateHostedZone:" in template
    assert "AWS::Route53::HostedZone" in template
    assert "DeletionPolicy: Retain" in _resource_block(template, "OpsPrivateHostedZone")
    assert "UpdateReplacePolicy: Retain" in _resource_block(template, "OpsPrivateHostedZone")
    assert "VPCRegion: !Ref AWS::Region" in template
    assert "route53:ChangeResourceRecordSets" in template
    assert "change-resource-record-sets" in template
    assert "Skipping Ops private DNS update because private IP was not available." in template
    assert "Route53 private DNS update skipped after retries; bootstrap continues." in template
    assert "OpsPrivateDnsName:" in template
    assert 'EnableOpsPrivateDns="${{ inputs.enable_ops_private_dns || \'true\' }}"' in workflow
    assert 'OpsPrivateDnsRecordName="${{ inputs.ops_private_dns_record_name || \'ops.dev.a360.internal\' }}"' in workflow


def test_ops_asg_uses_single_admin_instance_defaults_and_ec2_health():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/ops-deploy.yml").read_text(encoding="utf-8")

    assert "Default: t3.small" in template
    assert "HealthCheckType: EC2" in template
    assert "HealthCheckGracePeriod: 600" in template
    assert "MinInstancesInService: 0" in template
    assert "MaxBatchSize: 1" in template
    assert "default: t3.small" in workflow
    assert 'InstanceType="${{ inputs.instance_type || \'t3.small\' }}"' in workflow
    assert 'MaxSize="${{ inputs.max_size || \'1\' }}"' in workflow


def test_ops_deploy_workflow_builds_images_and_deploys_stack_with_same_tag():
    workflow = (ROOT / ".github/workflows/ops-deploy.yml").read_text(encoding="utf-8")
    tests_workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "branches:" in workflow
    assert "- dev" in workflow
    assert "- main" in workflow
    assert 'image_tag="${GITHUB_SHA::12}"' in workflow
    assert "ops-backend:${{ needs.meta.outputs.image_tag }}" in workflow
    assert "ops-ui:${{ needs.meta.outputs.image_tag }}" in workflow
    assert "rag-server:${{ needs.meta.outputs.image_tag }}" in workflow
    assert "aws cloudformation deploy" in workflow
    assert "aws cloudformation describe-stack-events" in workflow
    assert "environment: ops-deploy-${{ needs.meta.outputs.environment }}" in workflow
    assert "OPS_AUTO_DEPLOY" in workflow
    assert "AWS_DEPLOY_ROLE_ARN" in workflow
    assert "GHCR_TOKEN_SECRET_ARN" in workflow
    assert "OPS_RUNTIME_SECRET_ARN" in workflow
    assert "A360_BACKEND_URL" in workflow
    assert "GHCR_TOKEN_SECRET_ARN is empty" in workflow
    assert "OPS_RUNTIME_SECRET_ARN is empty" in workflow
    assert "A360_BACKEND_URL is empty" in workflow
    assert "EnableDefaultRagIngestSchedule=\"${{ inputs.enable_default_rag_ingest_schedule || 'true' }}\"" in workflow
    assert "infra-contract" in tests_workflow
    assert "python -m pytest tests/ -q" in tests_workflow
