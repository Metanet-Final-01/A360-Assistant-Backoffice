"""Action Chain P/R/F1 - §1(action_matching.py)에서 이미 확정된 1:1 매칭 쌍
위에서 순서 보존 정합성을 계산한다. 재설계 계획 v4 §5.

액션 매칭이 이미 확정된 상태이므로(Rule/Judge), 실제 벤더 WorFBench의
`t_eval_plan()`(`a360-eval-sandbox/external/WorFBench/evaluator/graph_evaluator.py:163-214`)
과 같은 방식으로 LIS(최장 증가 부분수열)를 주 계산으로 쓴다. 매칭이 이미
1:1로 고정된 상태에서는 LCS-DP로 계산해도 수학적으로 같은 값이 나와야 하므로,
같은 매칭 쌍 위에서 LCS-DP도 계산해 LIS와 값이 같은지 교차검증한다(다르면
매칭 로직에 버그가 있다는 신호).

주의: Action Chain F1은 "순서 정확도"가 아니라 "액션 선택 + 상대 순서를
함께 반영한 지표"다 - 액션 누락/불필요 액션도 분모(gold_count/pred_count)에
그대로 영향을 준다. 순수 순서만 보는 지표가 필요하면 나중에 별도로 추가한다."""

from __future__ import annotations

import bisect

from action_matching import ActionMatch, ScoredAction


def _lis_length(sequence: list[int]) -> int:
    """최장 증가 부분수열 길이, O(n log n) patience sorting."""
    tails: list[int] = []
    for x in sequence:
        pos = bisect.bisect_left(tails, x)
        if pos == len(tails):
            tails.append(x)
        else:
            tails[pos] = x
    return len(tails)


def _lcs_length_over_match_relation(
    gold_actions: list[ScoredAction], pred_actions: list[ScoredAction], can_match: set[tuple[str, str]]
) -> int:
    """LIS 결과를 교차검증하기 위한 LCS-DP. can_match(gold[i], pred[j])가
    확정된 매칭 쌍인지만 본다(임의의 유사도 행렬이 아니라 이미 정해진 관계)."""
    n, m = len(gold_actions), len(pred_actions)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if (gold_actions[i - 1].uid, pred_actions[j - 1].uid) in can_match:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m]


def compute_action_chain(
    gold_actions: list[ScoredAction], pred_actions: list[ScoredAction], matches: list[ActionMatch]
) -> dict:
    pred_order = {a.uid: i for i, a in enumerate(pred_actions)}
    gold_order = {a.uid: i for i, a in enumerate(gold_actions)}

    match_by_pred = {m.pred_id: m.gold_id for m in matches}
    matched_pred_ids_sorted = sorted(match_by_pred.keys(), key=lambda pid: pred_order[pid])
    gold_index_seq = [gold_order[match_by_pred[pid]] for pid in matched_pred_ids_sorted]

    lis_len = _lis_length(gold_index_seq)

    can_match = {(m.gold_id, m.pred_id) for m in matches}
    lcs_len = _lcs_length_over_match_relation(gold_actions, pred_actions, can_match)

    if lis_len != lcs_len:
        # 이 AssertionError는 의도적으로 여기서 raw하게 던진다 - 배치에서 한 케이스가
        # 이 예외로 실패해도 전체가 죽지는 않는다: run_eval_batch.py의 main()이
        # evaluate_case() 호출을 케이스 단위 try/except Exception으로 감싸고 있어서
        # (AssertionError도 Exception의 하위클래스라 그대로 잡힘) 해당 케이스만
        # status="error"로 summary row에 남고 나머지 케이스는 계속 진행된다 -
        # 실제로 13개 goldset 회귀에서 uid 중복 버그가 있던 케이스 1개가 이렇게
        # error row로만 남고 나머지 12개는 정상 완료된 것으로 확인함. 그래서 이
        # 함수 자체에서 try/except로 감싸 조용히 넘기지 않는다 - 매칭 로직 버그
        # 신호를 조용히 삼키면 잘못된 점수가 통과된 것처럼 보일 위험이 더 크다.
        raise AssertionError(
            f"LIS({lis_len})와 LCS({lcs_len})가 다름 - 매칭이 이미 1:1로 확정된 "
            "상태에서는 두 값이 같아야 정상. action_matching.py의 매칭 로직을 확인할 것."
        )

    chain_tp = lis_len
    precision = chain_tp / len(pred_actions) if pred_actions else 0.0
    recall = chain_tp / len(gold_actions) if gold_actions else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "chain_tp": chain_tp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "lis_length": lis_len,
        "lcs_length_crosscheck": lcs_len,
        "note": "Action Chain F1은 순서 정확도가 아니라 액션 선택+상대 순서를 함께 반영한 지표",
    }
