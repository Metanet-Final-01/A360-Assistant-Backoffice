"""청크 길이 심층 EDA — split/unsplit 분리, chunks_per_doc, 문서별 chars_per_token 분포,
gold 근거문서/실제 검색된 청크 분포까지 포함한 종합 분석.

배경: 얕은 평균/중앙값만으로는 "짧아서 안 쪼개진 문서"와 "실제로 쪼개진 긴 문서의 청크"가
섞여서 왜곡된다(사용자+GPT 지적, 2026-07-25). 이 스크립트는 그 혼합을 explicit하게 갈라서
본다. 경계 절단 품질 분석·ECDF/박스플롯 시각화는 범위 밖(별도 후속 작업).
"""
import json
from collections import defaultdict
import numpy as np
import pandas as pd
import psycopg
import tiktoken

NEON_DSN = "postgresql://neondb_owner:npg_BeZ3MdKR7IGE@ep-young-river-ao9slvcl-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
LOCAL_DSN = "host=127.0.0.1 port=5432 dbname=a360 user=a360_admin password=a360_local_password"
DATA_PATH = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\ops-server\backend\data\eval_runs.jsonl"
GOLDSET_PATH = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\ops-server\backend\app\eval\ragas_eval\cases\rag_goldset_v1.json"
OUT_PATH = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\deep_chunk_eda_2026-07-25.xlsx"

ENC = tiktoken.get_encoding("cl100k_base")

CANDIDATES = [(f"tok{n}", f"rag_documents_eval_tok{n}_ov0") for n in [128, 150, 250, 256, 300, 512, 900, 1000, 1024, 1200, 1500, 2048]] + \
             [(f"cs{n}", f"rag_documents_eval_cs{n}_ov0") for n in [300, 600, 900, 1200, 1500]]

PCTS = [10, 25, 50, 75, 90, 95, 99]


def pct_stats(arr, prefix):
    a = np.array(arr)
    if len(a) == 0:
        return {f"{prefix}_{p}": None for p in ["n", "mean", "std", "min", "max"] + [f"p{q}" for q in PCTS]}
    out = {f"{prefix}_n": len(a), f"{prefix}_mean": round(float(a.mean()), 1), f"{prefix}_std": round(float(a.std()), 1),
           f"{prefix}_min": int(a.min()), f"{prefix}_max": int(a.max())}
    for q in PCTS:
        out[f"{prefix}_p{q}"] = round(float(np.percentile(a, q)), 1)
    return out


# ---------- 1) 전체 코퍼스 source_type 매핑 ----------
local_conn = psycopg.connect(LOCAL_DSN)
lcur = local_conn.cursor()
lcur.execute("SELECT DISTINCT parent_id FROM rag_documents_eval_tok2048_ov0")
all_parents = [r[0] for r in lcur.fetchall()]

neon_conn = psycopg.connect(NEON_DSN)
ncur = neon_conn.cursor()
ncur.execute("SELECT parent_id, source_type, metadata->>'schema_source' FROM rag_documents WHERE parent_id = ANY(%s)", (all_parents,))
cat_map = {}
for pid, st, schema in ncur.fetchall():
    if st == "doc_page":
        cat_map[pid] = "doc_page"
    elif st == "action_schema":
        cat_map[pid] = f"action_schema-{schema or 'unknown'}"
    else:
        cat_map[pid] = f"{st}-{schema}" if schema else st
neon_conn.close()

# ---------- 2) 골드셋 케이스 -> reference_doc_ids, retrieved_parent_ids(raw) ----------
goldset = json.load(open(GOLDSET_PATH, encoding="utf-8"))
active_cases = {c["case_id"]: c for c in goldset if c.get("status") == "approved" and c.get("dataset_membership") == "active"}
gold_ref_docs = set()
for c in active_cases.values():
    for d in (c.get("reference_doc_ids") or []):
        gold_ref_docs.add(d)

retrieved_by_label = defaultdict(list)  # label -> list of parent_id (모든 케이스에서 검색된 것, 중복 포함)
with open(DATA_PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        label = r.get("agent_label", "")
        if not label.endswith("_exaone40_full129"):
            continue
        raw = r.get("raw") or {}
        for pid in (raw.get("retrieved_parent_ids") or []):
            retrieved_by_label[label].append(pid)

# ---------- 3) 후보별 청크 원본 데이터 로드 + 통계 ----------
doc_level_rows = []       # 문서 단위(청크 가중 아님) 통계용 원자료
chunk_level_rows = []     # 청크 단위 원자료
chunks_per_doc_rows = []
split_unsplit_rows = []
ratio_rows = []
retrieved_rows = []

for name, table in CANDIDATES:
    lcur.execute(f"SELECT parent_id, content FROM {table}")
    data = lcur.fetchall()
    parent_ids = [d[0] for d in data]
    contents = [d[1] for d in data]
    char_lens = [len(c) for c in contents]
    tok_lens = [len(t) for t in ENC.encode_batch(contents)]

    parent_chunk_count = defaultdict(int)
    parent_char_lens = defaultdict(list)
    parent_tok_lens = defaultdict(list)
    for pid, cl, tl in zip(parent_ids, char_lens, tok_lens):
        parent_chunk_count[pid] += 1
        parent_char_lens[pid].append(cl)
        parent_tok_lens[pid].append(tl)

    # --- chunks_per_doc ---
    counts = list(parent_chunk_count.values())
    n_docs = len(counts)
    total_chunks = sum(counts)
    one_chunk_pct = round(100 * sum(1 for c in counts if c == 1) / n_docs, 1)
    row = {"candidate": name, "n_docs": n_docs, "total_chunks": total_chunks,
           "chunks_per_doc_mean": round(total_chunks / n_docs, 2),
           "chunks_per_doc_median": float(np.median(counts)),
           "chunks_per_doc_p90": float(np.percentile(counts, 90)),
           "max_chunks_per_doc": max(counts), "one_chunk_doc_%": one_chunk_pct}
    chunks_per_doc_rows.append(row)

    # --- split vs unsplit, 카테고리별 ---
    for cat in set(cat_map.get(pid, "unknown") for pid in parent_ids) | {"ALL"}:
        for split_status, is_split_fn in [("unsplit", lambda pid: parent_chunk_count[pid] == 1),
                                           ("split_chunks", lambda pid: parent_chunk_count[pid] > 1)]:
            sel_char, sel_tok = [], []
            for pid, cl, tl in zip(parent_ids, char_lens, tok_lens):
                if cat != "ALL" and cat_map.get(pid, "unknown") != cat:
                    continue
                if not is_split_fn(pid):
                    continue
                sel_char.append(cl)
                sel_tok.append(tl)
            if not sel_char:
                continue
            r = {"candidate": name, "category": cat, "group": split_status}
            r.update(pct_stats(sel_char, "char"))
            r.update(pct_stats(sel_tok, "token"))
            split_unsplit_rows.append(r)

    # --- 문서별 chars_per_token 비율 분포 (문서 가중 - 문서당 총 chars/총 tokens 하나의 비율값) ---
    for cat in set(cat_map.get(pid, "unknown") for pid in parent_ids) | {"ALL"}:
        ratios = []
        for pid in parent_chunk_count:
            if cat != "ALL" and cat_map.get(pid, "unknown") != cat:
                continue
            total_char = sum(parent_char_lens[pid])
            total_tok = sum(parent_tok_lens[pid])
            if total_tok > 0:
                ratios.append(total_char / total_tok)
        if not ratios:
            continue
        r = {"candidate": name, "category": cat}
        r.update(pct_stats(ratios, "chars_per_token"))
        ratio_rows.append(r)

    # --- 검색된(retrieved) 청크 분포 (골드셋 129건 실행 기준) ---
    label = f"{name}_exaone40_full129"
    retrieved_pids = retrieved_by_label.get(label, [])
    if retrieved_pids:
        content_by_pid_first_chunk = defaultdict(list)
        # retrieved_parent_ids가 어느 청크(id)인지까지는 저장 안 돼있어 parent 전체 청크 길이로 근사 불가;
        # 대신 해당 parent의 "그 후보 테이블에서의 총 길이/청크수 평균"으로 근사(청크 하나 대표값 없어 평균 사용)
        rt_char, rt_tok = [], []
        for pid in retrieved_pids:
            if pid in parent_char_lens:
                # 여러 청크 중 어느 게 검색됐는지 특정 안 되므로, 문서 평균 청크 길이로 근사
                rt_char.append(np.mean(parent_char_lens[pid]))
                rt_tok.append(np.mean(parent_tok_lens[pid]))
        if rt_char:
            r = {"candidate": name, "n_retrieved_calls": len(retrieved_pids), "n_distinct_docs_retrieved": len(set(retrieved_pids))}
            r.update(pct_stats(rt_char, "retrieved_char_approx"))
            r.update(pct_stats(rt_tok, "retrieved_token_approx"))
            retrieved_rows.append(r)

local_conn.close()

chunks_per_doc_df = pd.DataFrame(chunks_per_doc_rows)
split_unsplit_df = pd.DataFrame(split_unsplit_rows)
ratio_df = pd.DataFrame(ratio_rows)
retrieved_df = pd.DataFrame(retrieved_rows)

with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as writer:
    chunks_per_doc_df.to_excel(writer, sheet_name="chunks_per_doc", index=False)
    split_unsplit_df.to_excel(writer, sheet_name="split_vs_unsplit", index=False)
    ratio_df.to_excel(writer, sheet_name="문서별_chars_per_token", index=False)
    retrieved_df.to_excel(writer, sheet_name="검색된청크_근사분포(gold129)", index=False)

print("saved:", OUT_PATH)
print("\n=== chunks_per_doc (요약) ===")
print(chunks_per_doc_df.to_string(index=False))
