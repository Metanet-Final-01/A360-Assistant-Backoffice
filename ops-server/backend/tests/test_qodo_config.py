"""Qodo 리뷰 정책 래칫 (RPA-257).

## 왜 테스트로 고정하나

리뷰 설정은 아무도 안 보는 파일이라 조용히 되돌아간다. 실제로 이 레포는 백엔드가
CodeRabbit → Qodo로 전환한 뒤에도 **혼자 전환되지 않은 채** `.coderabbit.yaml`만
남아 있었고, 그 사이 PR들은 기본 설정으로 리뷰됐다 — 한국어 강제도, "심각한 결함만"도
적용되지 않은 상태였는데 리뷰가 오긴 오니 아무도 눈치채지 못했다.

특히 `.coderabbit.yaml`에는 **"관측 DB에 직접 붙지 말 것 — 반드시 admin API 경유"**
라는, RPA-255에서 뒤집힌 방침이 박혀 있었다. 죽은 설정이 되살아나면 리뷰어가 낡은
정책을 근거로 최신 코드를 지적하게 된다. 그래서 삭제 상태까지 여기서 못 박는다.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load_qodo_config() -> dict:
    with (ROOT / ".pr_agent.toml").open("rb") as config_file:
        return tomllib.load(config_file)


def test_qodo_config_is_parseable_and_coderabbit_is_gone() -> None:
    """두 리뷰어 설정이 공존하면 어느 쪽이 도는지 알 수 없다 — 전환은 삭제까지가 끝이다."""
    qodo = _load_qodo_config()

    assert not (ROOT / ".coderabbit.yaml").exists()
    assert qodo["config"]["response_language"] == "ko-KR"
    assert qodo["config"]["enable_comment_approval"] is False
    assert qodo["config"]["enable_auto_approval"] is False
    assert qodo["github_app"]["feedback_on_draft_pr"] is False
    assert set(qodo["github_app"]["pr_commands"]) == {"/review"}
    assert "**/data/**" in qodo["ignore"]["glob"]


def test_push_retriggers_review() -> None:
    """이 설정이 없으면 지적을 반영해도 리뷰가 갱신되지 않는다. 실제로 그래서 재리뷰를
    받으려면 PR을 close/reopen 해야 했다 — 사람이 매번 수동으로 하게 두면 안 된다."""
    qodo = _load_qodo_config()

    assert qodo["github_app"]["handle_push_trigger"] is True
    assert qodo["github_app"]["push_commands"] == ["/review"]


def test_review_scope_is_narrowed_to_real_defects() -> None:
    """범위를 넓히면 저심각 노이즈가 늘어 정작 심각한 지적이 묻힌다."""
    qodo = _load_qodo_config()
    reviewer = qodo["pr_reviewer"]
    suggestions = qodo["pr_code_suggestions"]

    assert reviewer["require_tests_review"] is True
    assert reviewer["require_security_review"] is True
    assert reviewer["require_ticket_analysis_review"] is True
    assert reviewer["require_estimate_effort_to_review"] is False
    assert reviewer["require_can_be_split_review"] is False
    assert "지적하지 않는다" in reviewer["extra_instructions"]
    assert suggestions["focus_only_on_problems"] is True
    assert suggestions["commitable_code_suggestions"] is False
    assert suggestions["suggestions_score_threshold"] == 7


def test_observability_guidance_matches_the_current_decision() -> None:
    """관측 조회는 **DB 직접 읽기가 정상 경로**다(RPA-255). 지침이 admin API 경유를
    요구하는 상태로 되돌아가면, 리뷰어가 이미 뒤집힌 방침으로 최신 코드를 지적한다."""
    qodo = _load_qodo_config()
    instructions = (
        qodo["pr_reviewer"]["extra_instructions"]
        + qodo["pr_code_suggestions"]["extra_instructions"]
    )

    assert "관측 DB를 직접 읽는 것이 정상 경로" in instructions
    assert "admin API를 경유해야 한다\"고 지적하지 않는다" in instructions
    # 뒤집힌 건 '어디서 읽나'뿐이고, 읽기 전용·폴백 금지는 오히려 강화됐다.
    assert "조용한 폴백 금지" in instructions
    assert "읽기 전용이 실제로 강제되는가" in instructions


def test_deployment_and_secret_guidance_survives() -> None:
    """재시작마다 사라지는 로컬 사본에 의존하는 코드를 실제로 배포에 올린 적이 있다 —
    그 관점과 시크릿 점검이 지침에서 빠지면 같은 실수를 리뷰가 못 잡는다."""
    instructions = _load_qodo_config()["pr_reviewer"]["extra_instructions"]

    assert "재배포·재시작마다 사라진다" in instructions
    assert "xtrace" in instructions
    assert ".env.example" in instructions
