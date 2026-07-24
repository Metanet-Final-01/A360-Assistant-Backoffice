from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    assert "HasOpsRuntimeSecret" in template
    assert "ReadOpsRuntimeSecret" in template
    assert "type == \"object\"" in template
    assert "secret_string" in template
    assert "--env-file /opt/a360/runtime.env" in template
    assert "!If [HasOpsRuntimeSecret, !Ref OpsRuntimeSecretArn, !Ref AWS::NoValue]" in template
    assert '"ParameterKey": "OpsRuntimeSecretArn", "ParameterValue": ""' in params


def test_ops_alb_remains_internal_and_limited_to_client_vpn_cidr():
    template = (ROOT / "infra/cloudformation/ops-stack.yml").read_text(encoding="utf-8")

    assert "Scheme: internal" in template
    assert "CidrIp: !Ref ClientVpnCidr" in template
    assert "Scheme: internet-facing" not in template


def test_ops_deploy_workflow_builds_images_and_deploys_stack_with_same_tag():
    workflow = (ROOT / ".github/workflows/ops-deploy.yml").read_text(encoding="utf-8")
    tests_workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "branches:" in workflow
    assert "- dev" in workflow
    assert 'image_tag="${GITHUB_SHA::12}"' in workflow
    assert "ops-backend:${{ needs.meta.outputs.image_tag }}" in workflow
    assert "ops-ui:${{ needs.meta.outputs.image_tag }}" in workflow
    assert "rag-server:${{ needs.meta.outputs.image_tag }}" in workflow
    assert "aws cloudformation deploy" in workflow
    assert "environment: ops-deploy-${{ needs.meta.outputs.environment }}" in workflow
    assert "OPS_AUTO_DEPLOY" in workflow
    assert "AWS_DEPLOY_ROLE_ARN" in workflow
    assert "GHCR_TOKEN_SECRET_ARN" in workflow
    assert "OPS_RUNTIME_SECRET_ARN" in workflow
    assert "A360_BACKEND_URL" in workflow
    assert "GHCR_TOKEN_SECRET_ARN is empty" in workflow
    assert "OPS_RUNTIME_SECRET_ARN is empty" in workflow
    assert "A360_BACKEND_URL is empty" in workflow
    assert "infra-contract" in tests_workflow
    assert "python -m pytest tests/ -q" in tests_workflow
