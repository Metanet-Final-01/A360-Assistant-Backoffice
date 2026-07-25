import json
from collections import defaultdict
import numpy as np
from scipy import stats

data_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\ops-server\backend\data\eval_runs.jsonl"
metrics_order = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness"]

# label -> case_id -> metric -> value
by_label_case = defaultdict(lambda: defaultdict(dict))
with open(data_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        label = r.get("agent_label", "")
        if not label.endswith("_exaone40_full129"):
            continue
        cid = r.get("case_id")
        for m in r.get("metrics", []):
            if m.get("value") is not None:
                by_label_case[label][cid][m["name"]] = m["value"]

pairs = [
    ("cs300_ov0", "tok250"),
    ("cs600_ov0", "tok512"),
    ("cs1200_ov0", "tok900"),
    ("cs1500_ov0", "tok1024"),
    ("tok1000", "tok1024"),  # "2^n이 더 좋다" 가설 검증 - 2.3% 차이뿐인 가장 타이트한 쌍
]


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
    """Holm-Bonferroni: p-value 오름차순 정렬 후 p_(i) <= alpha/(n-i+1) 이 깨지는
    지점부터는 전부 기각 실패. statsmodels 없이 수동 구현(scipy에는 없음)."""
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    reject = [False] * n
    for rank, idx in enumerate(order):
        threshold = alpha / (n - rank)
        if pvals[idx] <= threshold:
            reject[idx] = True
        else:
            break  # 한 번 깨지면 그 이후(더 큰 p) 전부 기각 실패
    return reject


rows = []
for label_a, label_b in pairs:
    la = f"{label_a}_exaone40_full129"
    cases_a = by_label_case[la]
    if label_b.startswith("tok"):
        lb = f"{label_b}_exaone40_full129"
    else:
        lb = f"{label_b}_ov0_exaone40_full129"
    cases_b = by_label_case[lb]

    common = sorted(set(cases_a.keys()) & set(cases_b.keys()))
    for m in metrics_order:
        va, vb = [], []
        for cid in common:
            if m in cases_a[cid] and m in cases_b[cid]:
                va.append(cases_a[cid][m])
                vb.append(cases_b[cid][m])
        if len(va) < 5:
            continue
        va = np.array(va)
        vb = np.array(vb)
        diffs = va - vb  # A(char) - B(tok)
        ci_lo, ci_hi = bootstrap_ci(diffs)
        try:
            w_stat, w_p = stats.wilcoxon(va, vb)
        except ValueError:
            w_p = float("nan")
        rows.append({
            "pair": f"{label_a} vs {label_b}", "metric": m, "n": len(va),
            "meanA": va.mean(), "meanB": vb.mean(), "diff": diffs.mean(),
            "ci_lo": ci_lo, "ci_hi": ci_hi, "wilcoxon_p": w_p,
        })

pvals = [r["wilcoxon_p"] for r in rows]
holm_reject = holm_correction(pvals, alpha=0.05)

print(f"{'pair':30s}{'metric':22s}{'n':>4s}{'meanA':>8s}{'meanB':>8s}{'diff':>8s}{'95%CI_low':>11s}{'95%CI_high':>11s}{'wilcoxon_p':>11s}{'CI_incl_0':>10s}{'holm_sig':>9s}")
prev_pair = None
for r, sig in zip(rows, holm_reject):
    if r["pair"] != prev_pair:
        print()
        prev_pair = r["pair"]
    includes0 = "yes" if r["ci_lo"] <= 0 <= r["ci_hi"] else "NO"
    print(f"{r['pair']:30s}{r['metric']:22s}{r['n']:>4d}{r['meanA']:>8.3f}{r['meanB']:>8.3f}{r['diff']:>8.3f}"
          f"{r['ci_lo']:>11.3f}{r['ci_hi']:>11.3f}{r['wilcoxon_p']:>11.4f}{includes0:>10s}{('YES' if sig else 'no'):>9s}")

print(f"\nHolm-corrected 유의(alpha=0.05) 결과 수: {sum(holm_reject)}/{len(holm_reject)}")
