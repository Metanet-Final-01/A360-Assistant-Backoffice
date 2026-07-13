"""RAG 문서 병합 테스트 (pytest).

select_better_version(패키지 버전 충돌 시 구버전을 other_versions_seen에 보관)이
실제로 packages.json에는 남지만 build_rag_documents가 만드는 최종 검색 대상
문서(metadata)에는 안 실리고 있었다 — 그 회귀를 막는다.
"""

from app.rag.build.merge import build_rag_documents


def _package(name: str = "Number", version: str = "3.8.0", other_versions_seen: list | None = None) -> dict:
    return {
        "package_name": name,
        "package_label": name,
        "package_description": "설명",
        "package_version": version,
        "source_jar": f"{name}.jar",
        "actions": [
            {"name": "add", "label": "더하기", "description": "", "return_type": "NUMBER",
             "return_label": "", "return_required": False, "parameters": []},
        ],
        **({"other_versions_seen": other_versions_seen} if other_versions_seen is not None else {}),
    }


def test_package_overview_carries_other_versions_seen_into_metadata():
    other_versions_seen = [{"package_version": "2.0.0", "source_jar": "old_repo/Number.jar"}]
    package = _package(other_versions_seen=other_versions_seen)

    rag_docs = build_rag_documents([package], docs=[], locale="ko-KR")
    overview = next(d for d in rag_docs if d["source_type"] == "package_overview")
    assert overview["metadata"]["other_versions_seen"] == other_versions_seen


def test_package_overview_defaults_to_empty_list_when_no_conflict():
    package = _package()
    rag_docs = build_rag_documents([package], docs=[], locale="ko-KR")
    overview = next(d for d in rag_docs if d["source_type"] == "package_overview")
    assert overview["metadata"]["other_versions_seen"] == []
