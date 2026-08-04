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
import itertools

from action_matching import ActionMatch, ScoredAction

# 분기 블록 순열이 조합 폭발하면 조용히 오래 도는 대신 즉시 드러나게 한다.
# 실측(2026-08-04, 확정 9개): 최대 6가지(0376의 3갈래 if) - 여유가 충분하다.
MAX_GOLD_ORDERS = 5000


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


def _build_order_items(gold_actions: list[ScoredAction], branch_groups: list[dict]) -> list[tuple]:
    """정답 액션 목록을 "순서가 고정된 액션"과 "순서를 바꿔도 되는 분기 블록"의
    중첩 구조로 만든다.

    `flatten_scored_actions`는 if의 본문과 elseIf/else를 **연속으로** 이어붙이므로,
    한 if 그룹에 속한 액션들은 평탄화된 목록에서 하나의 연속 구간을 차지한다.
    중첩된 if의 액션은 바깥 갈래의 uid 목록에 포함되므로 구간끼리는 포함 관계만
    있고 부분적으로 겹치지 않는다 - 그래서 구간을 재귀적으로 쪼갤 수 있다.

    핵심업무 분류로 액션이 빠질 수 있으므로 남아 있는 액션만 대상으로 하고,
    실제 액션이 남은 갈래가 2개 미만인 그룹은 순서에 영향을 주지 못하므로
    아예 제외한다(빈 갈래를 순열에 넣으면 경우의 수만 늘고 결과는 같다)."""
    index_of = {action.uid: i for i, action in enumerate(gold_actions)}

    groups: list[dict] = []
    for group in branch_groups:
        branches = []
        for branch in group["branches"]:
            indexes = sorted(index_of[uid] for uid in branch["uids"] if uid in index_of)
            if indexes:
                branches.append(indexes)
        if len(branches) >= 2:
            flat = [i for branch in branches for i in branch]
            groups.append({"start": min(flat), "end": max(flat), "branches": branches})

    # 시작이 같으면 바깥쪽(끝이 더 먼 것)을 먼저 잡아야 중첩이 올바르게 풀린다.
    groups.sort(key=lambda g: (g["start"], -g["end"]))

    def build(low: int, high: int) -> list[tuple]:
        items: list[tuple] = []
        position = low
        while position < high:
            group = next(
                (g for g in groups if g["start"] == position and g["end"] < high), None
            )
            if group is None:
                items.append(("action", position))
                position += 1
                continue
            items.append(
                ("branches", [build(branch[0], branch[-1] + 1) for branch in group["branches"]])
            )
            position = group["end"] + 1
        return items

    return build(0, len(gold_actions))


def _generate_gold_orders(items: list[tuple]) -> list[list[int]]:
    """제어흐름상 허용되는 정답 순서를 전부 만든다.

    갈래 **내부** 순서는 그대로 두고, 상호배타적인 갈래 **블록끼리만** 자리를
    바꾼다. `if/elseIf/else`는 실행 시 하나만 살아남으므로 블록 사이에는 실제
    선후관계가 없는데, 평탄화하면 파서가 정한 순서 하나가 정답인 것처럼 굳어져
    가짜 제약이 생긴다. 서로 다른 갈래의 액션을 뒤섞는 것은 허용하지 않는다 -
    A360의 분기는 병렬 실행이 아니라 택일이기 때문이다."""
    orders: list[list[int]] = [[]]
    for kind, payload in items:
        if kind == "action":
            options: list[list[int]] = [[payload]]
        else:
            options = []
            for permutation in itertools.permutations(payload):
                per_branch = [_generate_gold_orders(branch) for branch in permutation]
                for combination in itertools.product(*per_branch):
                    options.append([index for part in combination for index in part])
        orders = [order + option for order in orders for option in options]
        if len(orders) > MAX_GOLD_ORDERS:
            raise ValueError(
                f"분기 블록 순열이 {MAX_GOLD_ORDERS}가지를 넘었다 - 조합 폭발이므로 "
                "상한/가지치기 설계를 먼저 정할 것."
            )
    return orders


def compute_action_chain(
    gold_actions: list[ScoredAction],
    pred_actions: list[ScoredAction],
    matches: list[ActionMatch],
    *,
    gold_branch_groups: list[dict] | None = None,
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

    # 분기 블록 순열별로 LIS를 구해 최댓값을 쓴다(2026-08-04). 평탄화가 만든
    # 가짜 선후관계(else 본문이 if 본문보다 뒤여야 한다)를 제거하기 위한 것으로,
    # WorFBench 논문이 정답 그래프의 위상정렬을 열거해 최댓값을 쓰는 것과 같은
    # 목적이다. 매칭은 순열 생성 **전에** 이미 확정돼 있으므로, 순열마다 대응
    # 관계까지 유리하게 다시 고르는 일은 생기지 않는다.
    gold_orders = _generate_gold_orders(_build_order_items(gold_actions, gold_branch_groups or []))
    chain_tp = lis_len
    for order in gold_orders:
        position_of = {canonical_index: position for position, canonical_index in enumerate(order)}
        reordered = [position_of[index] for index in gold_index_seq]
        chain_tp = max(chain_tp, _lis_length(reordered))

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
        "gold_order_count": len(gold_orders),
        "branch_permutation_gain": chain_tp - lis_len,
        "note": "Action Chain F1은 순서 정확도가 아니라 액션 선택+상대 순서를 함께 반영한 지표. 상호배타적 분기 블록의 순서는 가짜 제약이라 순열 전체에서 최댓값을 쓴다.",
    }
