"""문서 파싱 에이전트 테스트 (pytest).

LLM은 app.core.llm.chat을 monkeypatch로 목킹해 결정적으로 검증한다 — 실제 OpenAI 호출·DB 없이
파싱 로직/계약/회귀를 확인한다. 크게 세 축:
- 골든: 리프 → 액션 추출/비액션 기각/JSON 교정/중복 제거
- 계약: 에이전트 출력(build_package_dict)을 build_rag_documents에 넣으면 action_schema가 나오는가
- 회귀: JAR 패키지는 schema_source="jar" 기본 유지, 에이전트 산출은 "llm_agent"로 분리
"""

import json

import pytest

from app.rag.agents import package_parser
from app.rag.agents.schema import ParsedAction, build_package_dict
from app.rag.build.merge import build_rag_documents


# ----------------------------------------------------------------------------- helpers
def _leaf(title: str, package: str = "Excel", html: dict | None = None) -> dict:
    return {
        "package_name": package,
        "title": title,
        "path_titles": [package, title],
        "url": f"https://docs.automationanywhere.com/{package}/{title}",
        "menu_id": "m-" + title,
        "structured_html": html if html is not None else {"tag": "div", "text": title},
    }


def _action_json(name, *, is_action=True, params=None, label=None, desc=None, return_type=None):
    # 응답 스키마는 ParsedAction 자체(감싸는 키 없음).
    return json.dumps({
        "is_action": is_action,
        "name": name,
        "label": label,
        "description": desc,
        "return_type": return_type,
        "return_label": None,
        "parameters": params or [],
    })


@pytest.fixture
def mock_chat(monkeypatch):
    """app.core.llm.chat을 목킹한다. responses(문자열 리스트)를 순서대로 반환하고 호출을 기록한다."""
    def install(responses):
        it = iter(responses)
        calls: list[dict] = []

        def fake_chat(messages, *, purpose, model=None, response_format=None):
            calls.append({"purpose": purpose, "model": model, "response_format": response_format})
            return next(it)

        monkeypatch.setattr("app.core.llm.chat", fake_chat)
        return calls

    return install


# ----------------------------------------------------------------------------- 골든
def test_parse_leaf_extracts_action_with_params(mock_chat):
    mock_chat([_action_json(
        "sendEmail", label="이메일 보내기", desc="메일 전송", return_type="String",
        params=[
            {"name": "to", "type": "String", "required": True, "label": "수신자"},
            {"name": "subject", "type": "String", "required": False, "label": "제목"},
        ],
    )])
    action = package_parser.parse_leaf("Email", _leaf("이메일 보내기", "Email"))
    assert isinstance(action, ParsedAction)
    assert action.name == "sendEmail"
    assert [p.name for p in action.parameters] == ["to", "subject"]
    assert action.parameters[0].required is True


def test_parse_leaf_rejects_non_action(mock_chat):
    mock_chat([_action_json("", is_action=False)])
    assert package_parser.parse_leaf("Email", _leaf("Email 패키지 개요", "Email")) is None


def test_parse_leaf_empty_name_is_rejected(mock_chat):
    # is_action=true여도 name이 비면 액션으로 인정하지 않는다(id 생성 불가).
    mock_chat([_action_json("   ", is_action=True)])
    assert package_parser.parse_leaf("Email", _leaf("모호한 문서", "Email")) is None


def test_chat_json_repairs_bad_json_once(mock_chat):
    # 첫 응답이 깨진 JSON → 1회 교정 후 성공. chat이 2번 호출돼야 한다.
    calls = mock_chat(["이건 JSON이 아님", _action_json("openWorkbook", label="통합문서 열기")])
    action = package_parser.parse_leaf("Excel", _leaf("통합문서 열기", "Excel"))
    assert action is not None and action.name == "openWorkbook"
    assert len(calls) == 2


def test_parse_leaf_repairs_when_name_missing(mock_chat):
    # 규격 위반(필수 name 누락, 예: 모델이 {"action":{...}} wrapper로 감싸버린 경우도 동일)
    # → ValidationError → chat_json이 1회 교정을 시도한다. 조용히 유실되면 안 된다(회귀).
    calls = mock_chat([
        json.dumps({"is_action": True, "label": "이름 없음"}),   # name 누락 → 검증 실패
        _action_json("recovered", label="복구됨"),
    ])
    action = package_parser.parse_leaf("Excel", _leaf("문서", "Excel"))
    assert action is not None and action.name == "recovered"
    assert len(calls) == 2


def test_parse_package_dedupes_by_name_keeping_richer(mock_chat):
    # 같은 name 두 번 → 파라미터가 더 많은 쪽을 남긴다(id 충돌 방지, jar_parser와 동일 정책).
    mock_chat([
        _action_json("openWorkbook", params=[{"name": "path", "type": "String", "required": True}]),
        _action_json("openWorkbook", params=[
            {"name": "path", "type": "String", "required": True},
            {"name": "password", "type": "String", "required": False},
        ]),
    ])
    leaves = [_leaf("열기 A", "Excel"), _leaf("열기 B", "Excel")]
    pkg = package_parser.parse_package("Excel", leaves)
    assert pkg is not None
    assert pkg["schema_source"] == "llm_agent"
    assert len(pkg["actions"]) == 1
    assert len(pkg["actions"][0]["parameters"]) == 2  # 풍부한 쪽 채택


def test_parse_package_returns_none_when_no_actions(mock_chat):
    mock_chat([_action_json("", is_action=False), _action_json("", is_action=False)])
    assert package_parser.parse_package("Docs", [_leaf("소개", "Docs"), _leaf("튜토리얼", "Docs")]) is None


# ----------------------------------------------------------------------------- run() + JAR 보호
def test_run_skips_jar_covered_packages(tmp_path, mock_chat):
    handoff = tmp_path / "agent_handoff.jsonl"
    with open(handoff, "w", encoding="utf-8") as f:
        f.write(json.dumps(_leaf("이메일 보내기", "Email"), ensure_ascii=False) + "\n")
        f.write(json.dumps(_leaf("통합문서 열기", "Excel"), ensure_ascii=False) + "\n")
    # Email은 JAR 커버 → 건너뛴다. Excel만 파싱되어야 하므로 chat은 1번만 호출.
    calls = mock_chat([_action_json("openWorkbook", label="통합문서 열기")])
    results = package_parser.run(handoff, jar_package_names=["Email"])
    assert len(calls) == 1
    assert [p["package_name"] for p in results] == ["Excel"]


def test_run_skips_jar_covered_by_fuzzy_name(tmp_path, mock_chat):
    # 문서 사이트가 "Python Script"로 발견한 패키지는 JAR "Python"과 같은 패키지 → 퍼지로 건너뜀.
    # exact 비교였다면 새 패키지로 파싱돼 JAR과 중복 action_schema가 유입됐을 케이스(RPA 리뷰).
    handoff = tmp_path / "agent_handoff.jsonl"
    with open(handoff, "w", encoding="utf-8") as f:
        f.write(json.dumps(_leaf("스크립트 실행", "Python Script"), ensure_ascii=False) + "\n")
    calls = mock_chat([_action_json("runScript")])  # 호출되면 안 됨
    results = package_parser.run(handoff, jar_package_names=["Python"])
    assert results == []
    assert len(calls) == 0


def test_run_limit_stops_at_package_boundary(tmp_path, mock_chat):
    # 두 패키지 PkgA(2리프)+PkgB(3리프), limit=2 → A는 완전 파싱, B는 시작조차 안 함.
    handoff = tmp_path / "agent_handoff.jsonl"
    with open(handoff, "w", encoding="utf-8") as f:
        for i in range(2):
            f.write(json.dumps(_leaf(f"A액션{i}", "PkgA"), ensure_ascii=False) + "\n")
        for i in range(3):
            f.write(json.dumps(_leaf(f"B액션{i}", "PkgB"), ensure_ascii=False) + "\n")
    calls = mock_chat([_action_json(f"a{i}") for i in range(2)])
    results = package_parser.run(handoff, limit=2)
    assert len(calls) == 2                                     # A의 2리프만
    assert [p["package_name"] for p in results] == ["PkgA"]    # B는 시작 안 함


def test_run_limit_never_splits_a_started_package(tmp_path, mock_chat):
    # 한 패키지의 리프 수가 limit을 넘어도, 시작한 패키지는 통째로 파싱한다(부분 패키지 금지).
    # 부분 패키지를 '완성된 것처럼' 적재하면 이후 covered로 취급돼 영영 못 채운다(RPA 리뷰).
    handoff = tmp_path / "agent_handoff.jsonl"
    with open(handoff, "w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps(_leaf(f"액션{i}", "Excel"), ensure_ascii=False) + "\n")
    calls = mock_chat([_action_json(f"act{i}") for i in range(3)])
    results = package_parser.run(handoff, limit=2)
    assert len(calls) == 3                        # limit=2여도 3리프 전부 파싱(중간 절단 안 함)
    assert len(results[0]["actions"]) == 3        # 부분 아님


# ----------------------------------------------------------------------------- 계약: build로 흘려보내기
def test_contract_agent_output_becomes_action_schema():
    pkg = build_package_dict(
        "Email",
        [ParsedAction(
            name="sendEmail", label="이메일 보내기", description="메일 전송", return_type="String",
            parameters=[{"name": "to", "type": "String", "required": True, "label": "수신자"}],
        )],
        package_label="이메일",
    )
    rag_docs = build_rag_documents([pkg], docs=[], locale="ko-KR")

    action_docs = [d for d in rag_docs if d["source_type"] == "action_schema"]
    assert len(action_docs) == 1
    a = action_docs[0]
    assert a["package_name"] == "Email"
    assert a["action_name"] == "sendEmail"
    assert a["metadata"]["schema_source"] == "llm_agent"   # 신뢰 등급 분리 확인
    assert "to" in a["content"]                            # 파라미터가 content에 반영

    overview = [d for d in rag_docs if d["source_type"] == "package_overview"]
    assert overview and overview[0]["metadata"]["schema_source"] == "llm_agent"


def test_regression_jar_package_defaults_to_jar_source():
    # schema_source가 없는(=JAR 파서 산출) 패키지는 기존대로 "jar"로 태깅되어야 한다(회귀).
    jar_pkg = {
        "package_name": "Excel",
        "package_label": "엑셀",
        "package_description": "엑셀 조작",
        "package_version": "5.1.0",
        "actions": [{
            "name": "openWorkbook", "label": "통합문서 열기", "description": "연다",
            "return_type": "Session", "return_label": None,
            "parameters": [{"name": "path", "type": "String", "required": True, "label": "경로"}],
        }],
    }
    rag_docs = build_rag_documents([jar_pkg], docs=[], locale="ko-KR")
    action_docs = [d for d in rag_docs if d["source_type"] == "action_schema"]
    assert action_docs and action_docs[0]["metadata"]["schema_source"] == "jar"
