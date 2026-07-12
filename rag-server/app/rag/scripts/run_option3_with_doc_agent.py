"""옵션 3: 옵션 2 + JAR 없는 패키지의 리프 문서를 LLM 파싱 에이전트로 액션 스키마화.

옵션 2까지는 JAR 없는 패키지가 action_candidate(파라미터 없는 미검증 후보)로만 들어가 추천
메뉴에 안 떴다. 옵션 3은 build 전에 parse-docs-agent를 돌려, 리프 문서(structured_html)에서
실제 액션+파라미터를 뽑아 packages.json에 schema_source="llm_agent"로 병합한다. 그 결과 이
패키지들도 action_schema로 적재돼 추천 후보가 된다(단, JAR과 구분되는 미검증 신뢰 등급).

순서 주의: parse-docs-agent가 export-naive-leaf-actions보다 먼저 와야, 에이전트가 액션을 뽑은
패키지는 packages.json에 올라 naive(후보 나열)가 중복으로 다시 넣지 않는다 — JAR>에이전트>naive.

⚠️ 선행 조건: OPENAI_API_KEY가 .env에 있어야 한다. 비용/시간은 AGENT_PARSE_LIMIT(리프 수 상한),
AGENT_PARSE_MODEL(모델)로 통제한다. JAR로 이미 커버된 패키지는 에이전트가 절대 건드리지 않는다.
"""

from _run_steps import run_steps

if __name__ == "__main__":
    run_steps([
        ["crawl"],
        # en-US도 크롤링해야 build-action-tree/export-*가 packages.json과 일치하는 진짜 영어
        # package_name을 뽑는다 (옵션 2 주석과 동일 이유).
        ["crawl", "--locale", "en-US"],
        ["build-action-tree"],
        ["export-for-agent"],       # JAR 없는 패키지 리프(structured_html) → agent_handoff.jsonl
        ["parse-docs-agent"],       # LLM 파싱 → packages.json 병합(schema_source=llm_agent)
        ["export-naive-leaf-actions"],  # 에이전트가 못 뽑은 나머지 리프는 후보로 폴백
        ["build", "--include-naive-leaf-actions"],
        ["ingest"],
    ])
