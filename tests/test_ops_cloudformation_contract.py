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
