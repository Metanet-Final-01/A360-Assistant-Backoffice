"""옵션 2: 옵션 1 + JAR 없는 패키지의 리프 문서도 action_candidate로 같이 적재.

리프가 진짜 액션인지 필터링은 안 한다(그건 미래 파싱 Agent 몫 — app/rag/README.md
"JAR 없는 패키지" 절 참고). 파라미터 스키마도 없다 — action_schema가 아니라
action_candidate로 들어가 추천 메뉴에는 안 뜨고 search_kb 검색에만 쓰인다.

`--clean` 인자로 실행하면 마지막 ingest가 기존 rag_documents/OpenSearch를 전부 지우고
이번 build 결과로 완전히 새로 채운다(재적재). 인자 없이 실행하면 기존 방식대로
upsert만 한다 — 이전 실행에서 만들어졌다가 이번 build에서 빠진 row(예: 이전엔 naive
candidate였는데 이번엔 다른 source_type으로 바뀐 문서)가 안 지워지고 그대로 남을 수
있다는 게 upsert 모드의 알려진 한계.
"""

from _run_steps import run_steps, wants_clean

if __name__ == "__main__":
    ingest_step = ["ingest", "--clean"] if wants_clean() else ["ingest"]
    run_steps([
        ["crawl"],
        # en-US도 크롤링해야 build-action-tree/export-naive-leaf-actions가 packages.json과
        # 일치하는 진짜 영어 package_name을 뽑는다 — 없으면 한국어 제목 기반 폴백으로 이름이
        # 어긋나, JAR 있는 패키지까지 action_candidate로 잘못 다시 적재될 수 있다.
        ["crawl", "--locale", "en-US"],
        ["build-action-tree"],
        ["export-naive-leaf-actions"],
        ["build", "--include-naive-leaf-actions"],
        ingest_step,
    ])
