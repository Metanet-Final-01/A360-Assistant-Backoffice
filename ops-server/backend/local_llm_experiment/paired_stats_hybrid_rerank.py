"""hybrid+rerank 4개 후보(cs1200_ov0/cs1200_ov120/tok900/tok1024) 간 케이스레벨 paired
유의성검정 - paired_stats.py(vector-only 단계)와 동일 방법론(bootstrap 95% CI + Wilcoxon +
Holm 보정)을 그대로 재사용, 대상만 _hybrid_rerank 라벨로 바꿈."""
import json
from collections import defaultdict
from itertools import combinations

import numpy as np
from scipy import stats

data_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\ops-server\backend\data\eval_runs.jsonl"
metrics_order = [
    "faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness",
    "hit_at_1", "hit_at_3", "hit_at_5", "reciprocal_rank", "evidence_coverage",
]

candidates = ["cs1200_ov0", "cs1200_ov120", "tok900", "tok1024"]

by_label_case = defaultdict(lambda: defaultdict(dict))
with open(data_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        label = r.get("agent_label", "")
        if not label.endswith("_hybrid_rerank"):
            continue
        cid = r.get("case_id")
        # append_run()은 append-only라 재실행(이어하기 gap-fill)된 케이스는 같은
        # case_id로 두 번째 레코드가 또 생긴다 - 나중 레코드가 최신이므로 그냥 덮어써서
        # 항상 마지막 레코드만 남긴다(중복으로 평균이 부풀려지는 걸 방지, 2026-07-26).
        by_label_case[label][cid] = {m["name"]: m["value"] for m in r.get("metrics", []) if m.get("value") is not None}


def bootstrap_ci(diffs, n_boot=10000, seed=42):
    rng = np.random.RandomState(seed)
    diffs = np.array(diffs)
    n = len(diffs)
    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        sample = rng.choice(diffs, size=n, replace=True)
        boot_means[i] = sample.mean()
    lo, hi = np.percentile(boot_means, [2.5, 97.5])
    return lo, hi


def holm_correction(pvals, alpha=0.05):
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    reject = [False] * n
    for rank, idx in enumerate(order):
        threshold = alpha / (n - rank)
        if pvals[idx] <= threshold:
            reject[idx] = True
        else:
            break
    return reject


rows = []
for label_a, label_b in combinations(candidates, 2):
    la, lb = f"{label_a}_hybrid_rerank", f"{label_b}_hybrid_rerank"
    cases_a, cases_b = by_label_case[la], by_label_case[lb]
    common = sorted(set(cases_a.keys()) & set(cases_b.keys()))
    for m in metrics_order:
        va, vb = [], []
        for cid in common:
            if m in cases_a[cid] and m in cases_b[cid]:
                va.append(cases_a[cid][m])
                vb.append(cases_b[cid][m])
        if len(va) < 5:
            continue
        va, vb = np.array(va), np.array(vb)
        diffs = va - vb
        if np.all(diffs == 0):
            w_p = float("nan")
        else:
            try:
                w_stat, w_p = stats.wilcoxon(va, vb)
            except ValueError:
                w_p = float("nan")
        ci_lo, ci_hi = bootstrap_ci(diffs)
        rows.append({
            "pair": f"{label_a} vs {label_b}", "metric": m, "n": len(va),
            "meanA": va.mean(), "meanB": vb.mean(), "diff": diffs.mean(),
            "ci_lo": ci_lo, "ci_hi": ci_hi, "wilcoxon_p": w_p,
        })

valid_idx = [i for i, r in enumerate(rows) if r["wilcoxon_p"] == r["wilcoxon_p"]]  # NaN 제외
pvals = [rows[i]["wilcoxon_p"] for i in valid_idx]
holm_reject_valid = holm_correction(pvals, alpha=0.05)
holm_reject = [False] * len(rows)
for idx, sig in zip(valid_idx, holm_reject_valid):
    holm_reject[idx] = sig

print(f"{'pair':28s}{'metric':20s}{'n':>4s}{'meanA':>8s}{'meanB':>8s}{'diff':>8s}{'95%CI_low':>11s}{'95%CI_high':>11s}{'wilcoxon_p':>11s}{'CI_incl_0':>10s}{'holm_sig':>9s}")
prev_pair = None
for r, sig in zip(rows, holm_reject):
    if r["pair"] != prev_pair:
        print()
        prev_pair = r["pair"]
    includes0 = "yes" if r["ci_lo"] <= 0 <= r["ci_hi"] else "NO"
    p_display = f"{r['wilcoxon_p']:.4f}" if r["wilcoxon_p"] == r["wilcoxon_p"] else "n/a(tied)"
    print(f"{r['pair']:28s}{r['metric']:20s}{r['n']:>4d}{r['meanA']:>8.3f}{r['meanB']:>8.3f}{r['diff']:>8.3f}"
          f"{r['ci_lo']:>11.3f}{r['ci_hi']:>11.3f}{p_display:>11s}{includes0:>10s}{('YES' if sig else 'no'):>9s}")

print(f"\nHolm-corrected 유의(alpha=0.05) 결과 수: {sum(holm_reject)}/{len(valid_idx)} (동점이라 검정 불가 {len(rows)-len(valid_idx)}건 제외)")

import csv
out_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\hybrid_rerank_paired_significance_2026-07-26.csv"
with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.writer(f)
    writer.writerow(["pair", "metric", "n", "meanA", "meanB", "diff", "ci_lo", "ci_hi", "wilcoxon_p", "ci_includes_0", "holm_significant"])
    for r, sig in zip(rows, holm_reject):
        writer.writerow([r["pair"], r["metric"], r["n"], round(r["meanA"], 4), round(r["meanB"], 4),
                          round(r["diff"], 4), round(r["ci_lo"], 4), round(r["ci_hi"], 4),
                          r["wilcoxon_p"] if r["wilcoxon_p"] == r["wilcoxon_p"] else None, r["ci_lo"] <= 0 <= r["ci_hi"], sig])
print(f"\nCSV 저장: {out_path}")
