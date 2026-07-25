"""원문(청킹 전) 길이 분포 재구성 — tok2048(overlap=0)의 parent_id별 청크를 합산해서
원문 길이를 근사한다. overlap=0이라 청크들을 이어붙이면 원문과 거의 같아진다(구분자로
쓰인 공백/개행 손실 정도만 오차). 90.4%는 애초에 1청크(=원문 그대로)라 오차 없음,
나머지 9.6%만 합산 근사.
"""
import json
from collections import defaultdict
import psycopg
import numpy as np
import pandas as pd
import tiktoken

NEON_DSN = "postgresql://neondb_owner:npg_BeZ3MdKR7IGE@ep-young-river-ao9slvcl-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
LOCAL_DSN = "host=127.0.0.1 port=5432 dbname=a360 user=a360_admin password=a360_local_password"
OUT_PATH = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\raw_document_length_2026-07-25.xlsx"

ENC = tiktoken.get_encoding("cl100k_base")
PCTS = [10, 25, 50, 75, 90, 95, 99]


def pct_stats(arr):
    a = np.array(arr)
    out = {"n": len(a), "mean": round(float(a.mean()), 1), "std": round(float(a.std()), 1),
           "min": int(a.min()), "max": int(a.max())}
    for q in PCTS:
        out[f"p{q}"] = round(float(np.percentile(a, q)), 1)
    return out


conn = psycopg.connect(LOCAL_DSN)
cur = conn.cursor()
cur.execute("SELECT parent_id, content FROM rag_documents_eval_tok2048_ov0 ORDER BY parent_id, chunk_index")
rows = cur.fetchall()
conn.close()

by_parent_char = defaultdict(str)
by_parent_tok = defaultdict(int)
for pid, content in rows:
    by_parent_char[pid] += content
for pid, text in by_parent_char.items():
    by_parent_tok[pid] = len(ENC.encode(text))

neon_conn = psycopg.connect(NEON_DSN)
ncur = neon_conn.cursor()
ncur.execute("SELECT parent_id, source_type, metadata->>'schema_source' FROM rag_documents WHERE parent_id = ANY(%s)", (list(by_parent_char.keys()),))
cat_map = {}
for pid, st, schema in ncur.fetchall():
    if st == "doc_page":
        cat_map[pid] = "doc_page"
    elif st == "action_schema":
        cat_map[pid] = f"action_schema-{schema or 'unknown'}"
    else:
        cat_map[pid] = f"{st}-{schema}" if schema else st
neon_conn.close()

by_cat_char = defaultdict(list)
by_cat_tok = defaultdict(list)
for pid in by_parent_char:
    cat = cat_map.get(pid, "unknown")
    by_cat_char[cat].append(len(by_parent_char[pid]))
    by_cat_tok[cat].append(by_parent_tok[pid])
    by_cat_char["ALL"].append(len(by_parent_char[pid]))
    by_cat_tok["ALL"].append(by_parent_tok[pid])

rows_out = []
for cat in by_cat_char:
    char_s = pct_stats(by_cat_char[cat])
    tok_s = pct_stats(by_cat_tok[cat])
    row = {"category": cat}
    row.update({f"char_{k}": v for k, v in char_s.items()})
    row.update({f"token_{k}": v for k, v in tok_s.items() if k != "n"})
    rows_out.append(row)

df = pd.DataFrame(rows_out).sort_values("char_n", ascending=False)
df.to_excel(OUT_PATH, index=False, sheet_name="원문(청킹전)길이분포")
print("saved:", OUT_PATH)
print(df.to_string(index=False))
