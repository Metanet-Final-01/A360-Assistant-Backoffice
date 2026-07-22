"""_annotate_session_platform — 세션 역할·플랫폼 메타 후처리 (R7/R8·R16의 v2 원료).

세션은 패키지 단위 유도: 파라미터/후보에 'session'이 보이는 패키지에서만 액션명 패턴으로
opener/closer를 표시한다. 플랫폼은 등기부 로스터 값을 각 액션 스키마에 복사한다.
"""

from app.rag.build.merge_v2 import _annotate_session_platform


def _doc(pkg, action, params=None, candidates=None, schema=True):
    md = {"param_candidates": candidates or [], "action_label_ko": None}
    if schema:
        md["schema"] = {"name": action, "label": None, "parameters": params}
    else:
        md["schema"] = None
    return {"source_type": "action_schema", "package_name": pkg, "action_name": action, "metadata": md}


def _registry(pkg, platform=None):
    return {"packages": [{"display_en": pkg, "platform": platform}]}


def test_session_roles_derived_only_in_session_using_packages():
    docs = [
        _doc("Google Sheets", "Open spreadsheet", params=[{"name": "Session name"}]),
        _doc("Google Sheets", "Set cell", params=[{"name": "Session name"}, {"name": "Cell address"}]),
        _doc("Google Sheets", "Close", params=[{"name": "Session name"}]),
        # 세션 개념 없는 패키지 — 'Open'이어도 역할 없음
        _doc("Folder", "Open", params=[{"name": "Folder path"}]),
    ]
    _annotate_session_platform(docs, _registry("Google Sheets"), {})
    roles = {d["action_name"]: d["metadata"]["schema"].get("session_role") for d in docs[:3]}
    assert roles == {"Open spreadsheet": "opener", "Set cell": None, "Close": "closer"}
    assert docs[3]["metadata"]["schema"].get("session_role") is None


def test_candidates_count_as_session_signal_and_schema_created_when_missing():
    # 보강 실패로 schema=None인 행에도 신호를 실어야 한다 (parameters=None → params_unknown 경로)
    # opener/closer를 짝으로 둔다 — 한쪽만 있으면 역할을 싣지 않는 게 현재 계약이다
    # (아래 test_unbalanced_session_roles_are_dropped 참고).
    docs = [
        _doc("Terminal Emulator", "Connect", candidates=["Session name"], schema=False),
        _doc("Terminal Emulator", "Disconnect", candidates=["Session name"], schema=False),
    ]
    _annotate_session_platform(docs, _registry("Terminal Emulator"), {})
    schema = docs[0]["metadata"]["schema"]
    assert schema["session_role"] == "opener"
    assert schema["parameters"] is None
    assert docs[1]["metadata"]["schema"]["session_role"] == "closer"


def test_overrides_win_over_name_patterns():
    # 'Initialize'/'Teardown'은 이름 패턴에 안 걸린다 — overrides 선언만으로 역할이 붙어야 한다.
    docs = [
        _doc("X", "Initialize", params=[{"name": "session"}]),
        _doc("X", "Teardown", params=[{"name": "session"}]),
    ]
    _annotate_session_platform(
        docs, _registry("X"),
        {"session_overrides": {"X": {"openers": ["Initialize"], "closers": ["Teardown"]}}},
    )
    assert docs[0]["metadata"]["schema"]["session_role"] == "opener"
    assert docs[1]["metadata"]["schema"]["session_role"] == "closer"


def test_unbalanced_session_roles_are_dropped():
    """opener만 있고 closer가 없으면 역할을 아예 싣지 않는다.

    백엔드 derive_session_registry는 session_role 하나만 읽는다. 한쪽만 등록하면
    검수 R8이 "세션을 안 닫았다"고 거짓 위반을 낸다 — 근거가 반쪽이면 판정을 켜지
    않는 쪽이 안전하다(2026-07-20 감사 M10).
    """
    docs = [_doc("Y", "Connect", params=[{"name": "Session name"}])]
    _annotate_session_platform(docs, _registry("Y"), {})
    assert docs[0]["metadata"]["schema"].get("session_role") is None


def test_platform_copied_to_every_action_schema():
    docs = [_doc("Apple Mail", "Send email", params=[{"name": "To"}])]
    _annotate_session_platform(docs, _registry("Apple Mail", {"macos": True, "windows": False}), {})
    assert docs[0]["metadata"]["schema"]["platform"] == {"macos": True, "windows": False}
