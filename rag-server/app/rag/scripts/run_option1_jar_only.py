"""옵션 1: JAR 스키마가 있는 패키지만 action_schema로 적재.

crawl(공식 문서, 재시작 안전) -> build-action-tree(구조 확정) -> build -> ingest 순서로
실행한다. packages.json(JAR)/bots.jsonl은 이미 준비돼 있다고 가정한다 — parse-jars/
export-packages/bots는 zip 파일 경로나 Control Room 계정이 필요해 이 자동 실행에
넣을 수 없다(사람이 한 번 따로 실행해야 함).

`--clean` 인자로 실행하면 마지막 ingest가 완전 재적재, 인자 없으면 기존처럼 upsert만
한다(옵션 2/3과 동일한 토글).
"""

from _run_steps import run_steps, wants_clean

if __name__ == "__main__":
    ingest_step = ["ingest", "--clean"] if wants_clean() else ["ingest"]
    run_steps([
        ["crawl"],
        # en-US도 크롤링해야 build-action-tree가 packages.json과 일치하는 진짜 영어
        # package_name을 뽑는다 — 없으면 한국어 제목 기반 폴백으로 이름이 어긋날 수 있다.
        ["crawl", "--locale", "en-US"],
        ["build-action-tree"],
        ["build"],
        ingest_step,
    ])
