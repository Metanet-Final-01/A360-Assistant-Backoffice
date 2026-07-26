"""Hit@1/3/5(질문별 0/1 이진값)는 Wilcoxon보다 McNemar exact test가 맞다는 지적(GPT, 2026-07-26)
반영 - paired binary 데이터 전용 검정으로 재검정한다. 연속형 지표(RAGAS 5개, MRR,
evidence_coverage)는 기존 Wilcoxon+bootstrap 결과를 그대로 유지하고 안 건드림 - Hit@K만
별도 family로 분리해 Holm 보정(지표군을 섞으면 안 된다는 지적도 같이 반영).

McNemar exact: A/B 중 한쪽만 맞은 case(discordant pair)만 써서, "A만 맞은 개수"와
"B만 맞은 개수"가 동일한가(=50:50)를 이항검정으로 본다. 둘 다 맞았거나 둘 다 틀린
케이스는 애초에 A/B 차이에 정보를 안 주므로 제외한다.
"""
import json
import collections
from scipy import stats


def load_dedup(path="data/eval_runs.jsonl"):
    """append-only 로그라 이어하기(gap-fill)로 같은 case_id가 중복될 수 있어 마지막
    레코드만 남긴다(2026-07-26 tok900 gap-fill에서 실제로 겪은 문제, 재사용)."""
    data = collections.defaultdict(lambda: collections.defaultdict(dict))
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            data[r["agent_label"]][r["case_id"]] = {m["name"]: m["value"] for m in r["metrics"] if m["value"] is not None}
    return data


def mcnemar_exact(a_wins, b_wins):
    """a_wins=A만 맞은 개수, b_wins=B만 맞은 개수. H0: discordant pair 중 A가 이길 확률=0.5."""
    n = a_wins + b_wins
    if n == 0:
        return float("nan"), n
    result = stats.binomtest(a_wins, n, p=0.5, alternative="two-sided")
    return result.pvalue, n


def holm(pvals, alpha=0.05):
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    reject = [False] * n
    for rank, idx in enumerate(order):
        if pvals[idx] <= alpha / (n - rank):
            reject[idx] = True
        else:
            break
    return reject


def main():
    data = load_dedup()
    metrics = ["hit_at_1", "hit_at_3", "hit_at_5"]

    pairs = [
        # hybrid+rerank 4후보 전수쌍
        ("cs1200_ov0_hybrid_rerank", "cs1200_ov120_hybrid_rerank"),
        ("cs1200_ov0_hybrid_rerank", "tok900_hybrid_rerank"),
        ("cs1200_ov0_hybrid_rerank", "tok1024_hybrid_rerank"),
        ("cs1200_ov120_hybrid_rerank", "tok900_hybrid_rerank"),
        ("cs1200_ov120_hybrid_rerank", "tok1024_hybrid_rerank"),
        ("tok900_hybrid_rerank", "tok1024_hybrid_rerank"),
        # 실제 크기 매칭 쌍(vector-only)
        ("cs1200_ov0_exaone40_full129", "tok900_exaone40_full129"),
        ("cs1500_ov0_exaone40_full129", "tok1024_exaone40_full129"),
        # anchor vs 5단계 정규화-fusion 승자 2개
        ("tok1024_hybrid_rerank", "tok1024_norm_ranking_hr"),
        ("tok1024_hybrid_rerank", "tok1024_norm_coverage_hr"),
        ("tok1024_norm_ranking_hr", "tok1024_norm_coverage_hr"),
    ]

    rows = []
    for la, lb in pairs:
        ca, cb = data.get(la, {}), data.get(lb, {})
        common = sorted(set(ca) & set(cb))
        for m in metrics:
            a_wins = b_wins = both = neither = 0
            for cid in common:
                if m not in ca[cid] or m not in cb[cid]:
                    continue
                va, vb = ca[cid][m], cb[cid][m]
                if va == 1.0 and vb == 1.0:
                    both += 1
                elif va == 0.0 and vb == 0.0:
                    neither += 1
                elif va == 1.0 and vb == 0.0:
                    a_wins += 1
                else:
                    b_wins += 1
            p, n_discordant = mcnemar_exact(a_wins, b_wins)
            rows.append({
                "pair": f"{la} vs {lb}", "metric": m,
                "both_correct": both, "neither_correct": neither,
                "a_only": a_wins, "b_only": b_wins, "n_discordant": n_discordant, "p": p,
            })

    valid = [i for i, r in enumerate(rows) if r["p"] == r["p"]]
    sig = holm([rows[i]["p"] for i in valid])
    holm_full = [False] * len(rows)
    for idx, s in zip(valid, sig):
        holm_full[idx] = s

    prev = None
    for r, s in zip(rows, holm_full):
        if r["pair"] != prev:
            print()
            print("===", r["pair"], "===")
            prev = r["pair"]
        pstr = f"{r['p']:.4f}" if r["p"] == r["p"] else "n/a(무차이)"
        print(f"  {r['metric']:10s} A만맞음={r['a_only']:>3d} B만맞음={r['b_only']:>3d} "
              f"(discordant n={r['n_discordant']:>3d}, 둘다맞음={r['both_correct']}, 둘다틀림={r['neither_correct']}) "
              f"p={pstr:>12s} holm_sig={'YES' if s else 'no'}")

    print(f"\n총 유효 검정 {len(valid)}개(McNemar family) 중 Holm 유의: {sum(holm_full)}개")

    import csv
    out_path = r"C:\Users\KOSA\Desktop\A360-Assistant-Backoffice\docs\ragas_eval_data_2026-07-23\mcnemar_hitk_2026-07-26.csv"
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["pair", "metric", "both_correct", "neither_correct", "a_only", "b_only", "n_discordant", "p", "holm_significant"])
        writer.writeheader()
        for row, s in zip(rows, holm_full):
            writer.writerow({**row, "holm_significant": s})
    print(f"CSV 저장: {out_path}")


if __name__ == "__main__":
    main()
