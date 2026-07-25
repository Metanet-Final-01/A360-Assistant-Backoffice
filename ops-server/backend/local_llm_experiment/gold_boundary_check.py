"""골드셋 정답 근거 스니펫(reference_contexts.snippet)이 각 chunk_size 후보에서
청크 하나에 온전히 들어있는지, 아니면 경계에서 잘려서 여러 청크에 흩어지는지 확인한다.
chunk_experiment_runner.py의 _compute_evidence_coverage와 같은 문자열 포함 판정
방식(공백 제거 후 substring)을 재사용하되, "검색된 결과 안에 있는가"가 아니라
"그 문서의 어느 한 청크 안에 있는가"를 직접 본다 — 검색/생성과 무관한 순수 청킹 품질 지표.
"""
import json
import re
from collections import defaultdict
import psycopg
import pandas as pd

GOLDSET_PATH = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\ops-server\backend\app\eval\ragas_eval\cases\rag_goldset_v1.json"
LOCAL_DSN = "host=127.0.0.1 port=5432 dbname=a360 user=a360_admin password=a360_local_password"
OUT_PATH = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\gold_boundary_check_2026-07-25.xlsx"

CANDIDATES = [(f"tok{n}", f"rag_documents_eval_tok{n}_ov0") for n in [128, 150, 250, 256, 300, 512, 900, 1000, 1024, 1200, 1500, 2048]] + \
             [(f"cs{n}", f"rag_documents_eval_cs{n}_ov0") for n in [300, 600, 900, 1200, 1500]]


def _remove_ws(text):
    return re.sub(r"\s+", "", text or "")


goldset = json.load(open(GOLDSET_PATH, encoding="utf-8"))
active = [c for c in goldset if c.get("status") == "approved" and c.get("dataset_membership") == "active"]

# case_id -> list of (source_document_id, snippet)
case_snippets = []
for c in active:
    for rc in (c.get("reference_contexts") or []):
        case_snippets.append({
            "case_id": c["case_id"], "parent_id": rc.get("source_document_id"),
            "snippet": rc.get("snippet") or "",
        })
print(f"검증 대상 snippet {len(case_snippets)}개 (케이스 {len(active)}건)")

conn = psycopg.connect(LOCAL_DSN)
cur = conn.cursor()

results = []
for name, table in CANDIDATES:
    parent_ids_needed = list(set(s["parent_id"] for s in case_snippets if s["parent_id"]))
    cur.execute(f"SELECT parent_id, content FROM {table} WHERE parent_id = ANY(%s)", (parent_ids_needed,))
    rows = cur.fetchall()
    chunks_by_parent = defaultdict(list)
    for pid, content in rows:
        chunks_by_parent[pid].append(_remove_ws(content))

    intact, split_or_missing, no_chunks_found = 0, 0, 0
    for s in case_snippets:
        pid = s["parent_id"]
        snippet_ws = _remove_ws(s["snippet"])
        if not snippet_ws:
            continue
        chunks = chunks_by_parent.get(pid)
        if not chunks:
            no_chunks_found += 1
            continue
        if any(snippet_ws in chunk for chunk in chunks):
            intact += 1
        else:
            split_or_missing += 1
    total = intact + split_or_missing + no_chunks_found
    results.append({
        "candidate": name, "total_snippets": total,
        "intact_in_single_chunk": intact,
        "split_or_missing": split_or_missing,
        "parent_doc_not_in_table": no_chunks_found,
        "intact_%": round(100 * intact / total, 1) if total else None,
    })
    print(f"{name}: intact={intact} split/missing={split_or_missing} parent_not_found={no_chunks_found}")

conn.close()

df = pd.DataFrame(results)
df.to_excel(OUT_PATH, index=False, sheet_name="gold_snippet_경계체크")
print("\nsaved:", OUT_PATH)
print(df.to_string(index=False))
