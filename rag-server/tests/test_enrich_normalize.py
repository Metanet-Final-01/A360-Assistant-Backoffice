"""enrich _apply — 빈 파라미터 결과의 '미상'(None) 정규화 (2026-07-19 실측 반영).

문서가 필드를 서술하되 필드명이 UI 스크린샷에만 있는 액션(Google Drive Move file 등)은
LLM이 이름을 지어내지 않아 빈 결과가 된다. 이를 []('없음 확정')로 적재하면 백엔드 검수
R2가 정당한 파라미터를 전건 위반 처리하므로 None('미상')으로 기록해야 한다.
"""

from app.rag.build.enrich_params import _apply


def _doc():
    return {
        "action_name": "Move file",
        "content": "패키지: X\n액션: Move file\n설명: 파일 이동\n파라미터:\n없음",
        "metadata": {},
    }


def test_empty_params_recorded_as_unknown_none():
    d = _doc()
    _apply(d, [], "settings")
    assert d["metadata"]["schema"]["parameters"] is None
    assert "미상" in d["content"]


def test_nonempty_params_kept_as_list():
    d = _doc()
    _apply(d, [{"name": "File path"}], "settings")
    assert d["metadata"]["schema"]["parameters"] == [{"name": "File path"}]
    assert "File path" in d["content"]
