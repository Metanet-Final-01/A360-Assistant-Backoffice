"""JAR 패키지 스키마 파싱 테스트 (pytest).

_dedupe_actions_by_name은 실제로 겪은 문제(커뮤니티 WebAutomation JAR가 같은 액션
이름을 파라미터 개수가 다른 구/신버전으로 중복 정의)를 고치는 로직인데 지금까지
전용 테스트가 없었다 — 여기서 그 회귀를 막는다.
"""

import io
import json
import zipfile

from app.rag.sources.jar_parser import parse_jar_bytes


def _make_jar(commands: list[dict], package_name: str = "TestPkg", locales: dict | None = None) -> bytes:
    package_json = {"name": package_name, "label": package_name, "commands": commands}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("package.json", json.dumps(package_json))
        if locales:
            z.writestr("locales/ko_KR.json", json.dumps(locales))
    return buf.getvalue()


def _command(name: str, label: str, attr_names: list[str]) -> dict:
    return {
        "name": name,
        "label": label,
        "attributes": [{"name": a, "type": "STRING", "rules": []} for a in attr_names],
    }


def test_parse_jar_without_duplicates_keeps_all_actions():
    data = _make_jar([
        _command("openBrowser", "Open", ["url"]),
        _command("closeBrowser", "Close", []),
    ])
    result = parse_jar_bytes(data, "test.jar")
    assert result is not None
    assert {a["name"] for a in result["actions"]} == {"openBrowser", "closeBrowser"}


def test_dedupe_keeps_richer_version_when_action_name_duplicated():
    """실측 WebAutomation JAR 시나리오 재현 — 같은 액션 이름이 파라미터 개수 다른
    두 버전으로 중복 정의됨. Union으로 합치지 않고 더 완전한(파라미터 많은) 쪽만 채택해야 한다."""
    data = _make_jar([
        _command("openBrowser", "Open (old)", ["url"]),
        _command("openBrowser", "Open (new)", ["url", "timeout", "headless"]),
        _command("closeBrowser", "Close", []),
    ])
    result = parse_jar_bytes(data, "webautomation.jar")
    assert result is not None
    assert len(result["actions"]) == 2  # 3개 커맨드 → 중복 제거 후 2개 액션

    open_action = next(a for a in result["actions"] if a["name"] == "openBrowser")
    assert len(open_action["parameters"]) == 3
    assert open_action["label"] == "Open (new)"  # 채택된 쪽의 메타데이터 전체가 남아야 함

    close_action = next(a for a in result["actions"] if a["name"] == "closeBrowser")
    assert len(close_action["parameters"]) == 0  # 중복 없는 액션은 그대로


def test_dedupe_handles_multiple_duplicate_groups_independently():
    """여러 액션이 동시에 중복돼도(실측처럼 17개) 서로 안 섞이고 각자 더 풍부한 쪽만 남는다."""
    data = _make_jar([
        _command("actionA", "A old", ["p1"]),
        _command("actionA", "A new", ["p1", "p2"]),
        _command("actionB", "B new", ["q1", "q2", "q3"]),
        _command("actionB", "B old", ["q1"]),  # 순서가 뒤바뀌어도(신버전이 먼저 나와도) 더 풍부한 쪽 채택
    ])
    result = parse_jar_bytes(data, "test.jar")
    assert result is not None
    assert len(result["actions"]) == 2
    by_name = {a["name"]: a for a in result["actions"]}
    assert len(by_name["actionA"]["parameters"]) == 2
    assert len(by_name["actionB"]["parameters"]) == 3
    assert by_name["actionB"]["label"] == "B new"


def test_dedupe_equal_param_count_keeps_first_but_still_logs(capsys):
    """파라미터 개수가 같은 순수 중복도 뒤엣것으로 덮이지 않게(new_count > existing_count일 때만
    교체) 하되, 중복이 있었다는 사실 자체는 항상 로그로 남아야 한다(가시화 목적)."""
    data = _make_jar([
        _command("sameCount", "First", ["p1", "p2"]),
        _command("sameCount", "Second", ["p1", "p2"]),
    ])
    result = parse_jar_bytes(data, "test.jar")
    assert result is not None
    assert len(result["actions"]) == 1
    assert result["actions"][0]["label"] == "First"  # new_count > existing_count가 아니므로 안 바뀜

    captured = capsys.readouterr()
    assert "중복 정의 발견" in captured.out
