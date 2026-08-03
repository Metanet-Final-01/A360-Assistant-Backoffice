"""후보 워크플로우들을 Exaone(로컬 llama.cpp 서버)에게 "골드셋으로 쓸 가치가 있는가"
빠르게 판정받는다. 판정에 필요한 배경지식(이 프로젝트 맥락 + 실제 RAG 카탈로그 설명)을
같이 넣어준다. reasoning on/off 둘 다 돌려서 비교한다.

최종 확정은 사람이 한다 - 이 스크립트는 1차 스크리닝 보조용.

실행 전제:
- 로컬 llama.cpp 서버가 EXAONE_BASE_URL에 떠 있어야 함 (OpenAI 호환 API)
- rag-server/.env 에 로컬 Postgres 접속 정보(DATABASE_HOST/PORT/USERNAME/PASSWORD)가 있어야 함
- 입력 후보 목록은 candidate_pool/goldset_candidates_148.json (또는 --candidates로 지정)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from path_utils import full_470_dataset_dir, goldset_expansion_dir, workspace_root  # noqa: E402

sys.path.insert(0, str(workspace_root() / "A360-Assistant-Ops-rpa187" / "scripts" / "agent_flow_eval" / "processing"))
from resolve_subtask_coverage import resolve_transitive  # noqa: E402

from openai import OpenAI  # noqa: E402
import psycopg  # noqa: E402

EXAONE_BASE_URL = "http://192.168.1.147:8820/v1"
EXAONE_MODEL = "EXAONE-4.0-32B"

DATASET = full_470_dataset_dir()
DEFAULT_CANDIDATES = goldset_expansion_dir() / "candidate_pool" / "goldset_candidates_148.json"
DEFAULT_LOG_DIR = goldset_expansion_dir() / "exaone_judge_logs_148"

client = OpenAI(base_url=EXAONE_BASE_URL, api_key="not-needed")


def _load_local_pg_dsn() -> str:
    """rag-server/.env의 로컬 DATABASE_* 값을 읽어 DSN을 조립한다. 값 자체는 출력 안 함."""
    env_path = workspace_root() / "A360-Assistant-Ops" / "rag-server" / ".env"
    text = env_path.read_text(encoding="utf-8")

    def get(key: str) -> str:
        m = re.search(rf"^{key}=(.*)$", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    host = get("DATABASE_HOST") or "localhost"
    port = get("DATABASE_PORT") or "5433"
    user = get("DATABASE_USERNAME") or "a360_admin"
    password = get("DATABASE_PASSWORD")
    name = "a360"
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def fetch_action_docs(pairs: set[tuple[str, str]], pg_dsn: str) -> dict[tuple[str, str], str]:
    """(package, action) -> "title: content 앞부분" 카탈로그 설명. 못 찾으면 항목 생략."""
    docs: dict[tuple[str, str], str] = {}
    try:
        with psycopg.connect(pg_dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                for pkg, act in pairs:
                    cur.execute(
                        "SELECT title, content FROM rag_documents "
                        "WHERE source_type='action_schema' AND package_name=%s AND action_name=%s LIMIT 1",
                        (pkg, act),
                    )
                    row = cur.fetchone()
                    if row:
                        title, content = row
                        docs[(pkg, act)] = f"{title}: {(content or '')[:200]}"
    except Exception as e:  # noqa: BLE001 - 배경지식은 있으면 좋고 없어도 판정은 진행
        print(f"  (RAG 문서 조회 실패, 배경지식 없이 진행: {e})")
    return docs


def build_prompt(bot_name: str, action_labels: list[str], action_docs: dict[tuple[str, str], str]) -> str:
    doc_lines = []
    seen = set()
    for label in action_labels:
        pkg, _, act = label.partition(".")
        key = (pkg, act)
        if key in seen:
            continue
        seen.add(key)
        doc = action_docs.get(key)
        if doc:
            doc_lines.append(f"- {label}: {doc}")
        else:
            doc_lines.append(f"- {label}: (카탈로그 설명 없음)")

    return f"""당신은 RPA 워크플로우 평가용 골드셋(정답 데이터셋)을 만드는 작업을 돕습니다.

[배경]
이 골드셋은 "업무정의서(자연어 문서)를 보고 AI 에이전트가 올바른 자동화 워크플로우를
생성할 수 있는지" 평가하는 데 씁니다. 좋은 골드셋 후보는:
- 실제로 의미 있는 업무 프로세스를 수행함 (단순 로그 남기기, 변수 대입 같은 잡무만 있는 건 아님)
- 업무정의서만 보고도 추론 가능한 구체적 로직을 가짐
- 지나치게 사소하거나(액션 2~3개짜리 장난 수준), 반대로 지나치게 특수한 케이스만은 아님

[이 후보의 실제 액션 시퀀스] (봇: {bot_name})
{chr(10).join(f"{i+1}. {label}" for i, label in enumerate(action_labels))}

[각 액션의 실제 카탈로그 설명 (참고용 배경지식)]
{chr(10).join(doc_lines)}

[질문]
이 워크플로우를 골드셋(평가용 정답 데이터)으로 쓸 가치가 있습니까?
아래 형식으로 짧게 답하세요:
가치: (있음/없음/애매함)
이유: (한두 문장)
"""


def judge(prompt: str, enable_thinking: bool) -> dict:
    start = time.time()
    resp = client.chat.completions.create(
        model=EXAONE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    elapsed = time.time() - start
    return {
        "content": resp.choices[0].message.content,
        "elapsed_seconds": round(elapsed, 2),
        "enable_thinking": enable_thinking,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    args = parser.parse_args()

    args.log_dir.mkdir(parents=True, exist_ok=True)
    pg_dsn = _load_local_pg_dsn()
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))

    results = []
    for idx, r in enumerate(candidates, 1):
        bot_name = r["bot_name"]
        source_file = r["source_file"]
        bot_dir = DATASET / bot_name
        stem = source_file[: -len(".goldset.json")]

        log_path = args.log_dir / f"{bot_name}__{stem}.json"
        if log_path.exists():
            print(f"[{idx}/{len(candidates)}] {bot_name} - 이미 완료, 건너뜀 (재개)")
            results.append(json.loads(log_path.read_text(encoding="utf-8")))
            continue

        pairs, unresolved = resolve_transitive(bot_dir / "workflows", stem, visited=set())
        action_labels = sorted({f"{p}.{a}" for p, a in pairs})
        action_docs = fetch_action_docs(set(pairs), pg_dsn)
        prompt = build_prompt(bot_name, action_labels, action_docs)

        print(f"[{idx}/{len(candidates)}] {bot_name} ({source_file}) - 액션 {len(pairs)}개")
        case_result = {"bot_name": bot_name, "source_file": source_file, "action_count": len(pairs), "prompt": prompt}

        for enable_thinking in (True, False):
            label = "reasoning_on" if enable_thinking else "reasoning_off"
            print(f"  {label} 호출 중...", flush=True)
            out = judge(prompt, enable_thinking)
            print(f"  -> {out['elapsed_seconds']}초")
            case_result[label] = out

        results.append(case_result)
        log_path.write_text(json.dumps(case_result, ensure_ascii=False, indent=2), encoding="utf-8")

    (args.log_dir / "_all_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n완료: {args.log_dir}")


if __name__ == "__main__":
    main()
