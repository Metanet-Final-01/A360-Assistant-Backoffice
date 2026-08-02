"""Rule Match + Judge Match 기반 Action 매칭 엔진. 재설계 계획(2026-07-30,
`peaceful-watching-possum.md`) v4의 §1~§4 구현.

기존 `run_eval_case.py`의 `Counter & Counter` multiset 방식은 canonical
label별 개수만 세고 "어느 gold가 어느 pred랑 매칭됐는지"는 안 남긴다 - attribute
비교와 순서(chain) 비교를 하려면 명시적인 1:1 쌍이 필요해서 이 모듈을 새로
만든다. 기존 `load_action_equivalence_map`/`canonicalize_actions`은 여기로
옮기고 `run_eval_case.py`/`pm4py_adapter.py`가 여기서 import한다."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

EVAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_ROOT.parent))
sys.path.insert(0, str(EVAL_ROOT / "critical_attribute"))

from action_filters import (  # noqa: E402
    action_label,
    is_control_flow_marker_action,
    is_disabled_step,
    is_formatting_only_action,
    is_session_lifecycle_action,
)
from attribute_signature import common_signature  # noqa: E402
from judge import judge_action_equivalence  # noqa: E402

# 공통 보조 액션(로깅/확인팝업/화면캡처) - 실제 업무 로직이 아니라 어느 구현이든
# 거의 항상 붙는 부수 작업이라 gold/pred 양쪽 다 채점에서 제외한다. gold_core_actions
# (§7, uid 단위 수동 제외)는 이번 정답의 "수동 서식 반복" 같은 케이스별 구현기교를
# 사람이 판단해서 빼는 것이고, 이건 그것과 별개로 "패키지 자체가 거의 항상
# 부수적"인 범용 규칙 - 매번 사람이 판단할 필요 없이 항상 적용한다.
# 실제로 발견된 문제: v3 예측이 "Logging"(gold의 "LogToFile"과 다른 이름) 패키지를
# 썼는데 gold_core_actions는 gold 쪽에만 적용돼서 예측 쪽 Logging/MessageBox가
# 그대로 FP로 남았음 - 이걸 고치는 것.
NOISE_PACKAGES = {"LogToFile", "Logging", "MessageBox", "Screen"}


@dataclass
class ScoredAction:
    uid: str
    package: str | None
    action: str | None
    canonical_label: str
    common: dict = field(default_factory=dict)

    @property
    def raw_label(self) -> str:
        return action_label(self.package, self.action)


@dataclass
class ActionMatch:
    gold_id: str
    pred_id: str
    match_type: Literal["rule", "judge"]
    canonical_label: str


def normalize_action_label(label: str) -> str:
    return "".join(label.split()).casefold()


def load_action_equivalence_map(root: Path | None = None) -> dict[str, str]:
    """동치 규칙을 대소문자와 공백에 무관한 조회 표로 읽는다."""
    root = root or EVAL_ROOT
    path = root / "action_equivalence_rules.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for group in payload.get("equivalence_groups", []) or []:
        canonical = group.get("canonical")
        if not canonical:
            continue
        for member in group.get("members", []) or []:
            key = normalize_action_label(member)
            if key in mapping and mapping[key] != canonical:
                raise ValueError(f"Action equivalence member maps to multiple canonicals: {member}")
            mapping[key] = canonical
        mapping.setdefault(normalize_action_label(canonical), canonical)
    return mapping


def load_conditional_equivalence_groups(root: Path | None = None) -> list[dict]:
    root = root or EVAL_ROOT
    path = root / "action_equivalence_rules_conditional.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("conditional_equivalence_groups", []) or []


def canonicalize_action(
    package: str | None,
    action: str | None,
    common: dict,
    plain_map: dict[str, str],
    conditional_groups: list[dict],
) -> str:
    """조건부 규칙(operation 값까지 맞아야 함) 먼저 확인, 안 맞으면 순수
    package.action alias 표, 그것도 없으면 원본 라벨 그대로."""
    for group in conditional_groups:
        for member in group.get("members", []) or []:
            if member.get("package") != package or member.get("action") != action:
                continue
            when = member.get("when", {}) or {}
            if all(common.get(key) == value for key, value in when.items()):
                return group["canonical"]

    label = action_label(package, action)
    normalized_label = normalize_action_label(label)
    return plain_map.get(normalized_label, normalized_label)


def load_gold_core_actions(path: Path | None) -> dict[str, bool] | None:
    """§7: uid별 include/exclude 고정 파일. 없으면 None(전부 포함)."""
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["uid"]: row["include"] for row in payload.get("core_actions", []) or []}


def flatten_scored_actions(
    steps: list[dict[str, Any]],
    *,
    include_map: dict[str, bool] | None = None,
    plain_map: dict[str, str],
    conditional_groups: list[dict],
    in_catch: bool = False,
    _counter: list[int] | None = None,
) -> list[ScoredAction]:
    """steps 트리에서 action 스텝만 뽑아 ScoredAction으로 만든다. disabled/
    session-lifecycle/control-flow-marker/formatting-only/catch 블록은 항상
    제외(기존 run_eval_case.py와 동일 원칙). include_map이 주어지면(gold 쪽만) uid가 명시적으로
    include=False인 것도 제외한다(§7 구현 기교 필터).

    주의 1: `convert_backend_recommendation.py`로 변환된 예측 스텝에는 "uid"
    필드가 아예 없다(원본 AA workflow에만 있는 필드) - uid가 없으면
    `f"idx_{순번}"`으로 합성 id를 만든다. 이걸 안 하면 예측 액션 전부가
    uid=None으로 겹쳐서 매칭 결과가 전부 마지막 액션 하나로 뭉개지는 버그가
    생긴다(실제로 한 번 발생해서 고침).

    주의 2: 정답(gold) 쪽도 같은 uid가 flatten된 목록에 "두 번 이상" 나올 수
    있다(TaskBot.runTask로 참조된 하위 워크플로우가 여러 호출 지점에 inline돼서
    같은 원본 스텝 uid가 여러 실제 occurrence로 나타남 - 실제 goldset
    "0338_lettergenerationbot"에서 확인됨). uid만 키로 쓰면 각 occurrence의
    실제 위치(순서)를 구분 못 해서 action_chain.py의 LIS 계산이 잘못된 위치를
    가리키는 버그가 생긴다(실제로 발생: LIS != LCS 교차검증에서 걸림). 그래서
    ScoredAction.uid는 항상 `f"{원본uid 또는 idx}#{전역 occurrence 순번}"`으로
    유일하게 만들고, gold_core_actions include/exclude 조회만 원본 uid
    기준으로 한다(사람이 작성한 include/exclude 파일은 원본 uid를 참조하므로)."""
    if _counter is None:
        _counter = [0]
    out: list[ScoredAction] = []
    for step in steps:
        if is_disabled_step(step):
            continue
        step_type = step.get("type")
        if step_type == "action":
            package, action = step.get("package"), step.get("action")
            if is_session_lifecycle_action(package, action):
                continue
            if is_control_flow_marker_action(package, action):
                continue
            if is_formatting_only_action(package, action):
                continue
            if package in NOISE_PACKAGES:
                continue
            if in_catch:
                continue
            raw_uid = step.get("uid") or f"idx_{_counter[0]}"
            if include_map is not None and include_map.get(raw_uid) is False:
                _counter[0] += 1
                continue
            unique_uid = f"{raw_uid}#{_counter[0]}"
            _counter[0] += 1
            common = common_signature(step)
            canonical = canonicalize_action(package, action, common, plain_map, conditional_groups)
            out.append(ScoredAction(uid=unique_uid, package=package, action=action, canonical_label=canonical, common=common))
            continue
        if step_type == "container":
            out.extend(flatten_scored_actions(step.get("steps", []) or [], include_map=include_map, plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter))
        elif step_type in {"if", "loop", "trigger_loop"}:
            out.extend(flatten_scored_actions(step.get("steps", []) or [], include_map=include_map, plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter))
            for branch in step.get("branches", []) or []:
                out.extend(flatten_scored_actions(branch.get("steps", []) or [], include_map=include_map, plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter))
        elif step_type == "try":
            out.extend(flatten_scored_actions(step.get("steps", []) or [], include_map=include_map, plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter))
            for branch in step.get("branches", []) or []:
                branch_in_catch = in_catch or branch.get("branch") == "catch"
                out.extend(flatten_scored_actions(branch.get("steps", []) or [], include_map=include_map, plain_map=plain_map, conditional_groups=conditional_groups, in_catch=branch_in_catch, _counter=_counter))
        else:
            raise ValueError(f"Unknown step type: {step_type!r}")
    return out


def pair_rule_matches(
    gold_actions: list[ScoredAction], pred_actions: list[ScoredAction]
) -> tuple[list[ActionMatch], list[ScoredAction], list[ScoredAction]]:
    """같은 canonical_label끼리, common_signature가 완전히 같은 쌍부터 우선
    배정하고 남은 건 등장 순서대로 배정한다(§1a). 반환: (매칭들, 남은 gold, 남은 pred)."""
    matches: list[ActionMatch] = []
    remaining_gold = list(gold_actions)
    remaining_pred = list(pred_actions)

    labels = {a.canonical_label for a in remaining_gold} & {a.canonical_label for a in remaining_pred}
    for label in labels:
        g_group = [a for a in remaining_gold if a.canonical_label == label]
        p_group = [a for a in remaining_pred if a.canonical_label == label]

        # 1) common_signature 완전히 같은 쌍 우선
        used_p: set[str] = set()
        paired_g: set[str] = set()
        for g in g_group:
            for p in p_group:
                if p.uid in used_p:
                    continue
                if g.common == p.common and g.common:
                    matches.append(ActionMatch(g.uid, p.uid, "rule", label))
                    used_p.add(p.uid)
                    paired_g.add(g.uid)
                    break

        # 2) 남은 것은 등장 순서대로 배정
        left_g = [g for g in g_group if g.uid not in paired_g]
        left_p = [p for p in p_group if p.uid not in used_p]
        for g, p in zip(left_g, left_p):
            matches.append(ActionMatch(g.uid, p.uid, "rule", label))

    matched_g_uids = {m.gold_id for m in matches}
    matched_p_uids = {m.pred_id for m in matches}
    remaining_gold = [a for a in remaining_gold if a.uid not in matched_g_uids]
    remaining_pred = [a for a in remaining_pred if a.uid not in matched_p_uids]
    return matches, remaining_gold, remaining_pred


def _embedding_text(a: ScoredAction) -> str:
    return f"{a.raw_label} {a.common.get('operation') or ''} {a.common.get('target_text') or ''}".strip()


def _embed(texts: list[str]) -> list[list[float]]:
    from judge import _get_client  # 이미 있는 OpenAI 클라이언트 재사용(API 키 로딩 포함)

    client = _get_client()
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def pair_judge_matches(
    remaining_gold: list[ScoredAction], remaining_pred: list[ScoredAction]
) -> tuple[list[ActionMatch], list[dict]]:
    """§1b: 임베딩 유사도 상호 Top-1인 쌍만 LLM Judge에 넘긴다. same이면 매칭,
    아니면 Unmatched. 반환: (매칭들, judge 호출 로그).

    최소 유사도 컷오프는 두지 않는다 - 남은 후보 각각의 "억지 1등" 여부까지
    걸러내려던 값이었는데, 13개 goldset 정도로만 보정한 잠정 임계값(0.3)을
    실제 검증 없이 매칭 게이트로 계속 쓰는 게 근거가 약하다는 지적을 받아
    제거함(judge_log에는 similarity 값을 계속 기록하므로 나중에 실제 분포를
    보고 필요하면 근거 있는 값으로 다시 넣을 수 있다). 상호 Top-1이라는 조건
    자체가 이미 "둘 다 서로를 가장 가깝다고 본 유일한 쌍"이라는 강한 필터이고,
    최종 same/different 판정은 어차피 Judge가 하므로 이 컷오프 없이도 임의
    배정 문제는 생기지 않는다."""
    if not remaining_gold or not remaining_pred:
        return [], []

    gold_texts = [_embedding_text(a) for a in remaining_gold]
    pred_texts = [_embedding_text(a) for a in remaining_pred]
    gold_vecs = _embed(gold_texts)
    pred_vecs = _embed(pred_texts)

    sim = [[_cosine(gv, pv) for pv in pred_vecs] for gv in gold_vecs]

    gold_top1 = [max(range(len(pred_vecs)), key=lambda j: sim[i][j]) for i in range(len(gold_vecs))]
    pred_top1 = [max(range(len(gold_vecs)), key=lambda i: sim[i][j]) for j in range(len(pred_vecs))]

    matches: list[ActionMatch] = []
    judge_log: list[dict] = []
    for i, g in enumerate(remaining_gold):
        j = gold_top1[i]
        if pred_top1[j] != i:
            continue
        score = sim[i][j]
        p = remaining_pred[j]
        result = judge_action_equivalence(g.raw_label, g.common, p.raw_label, p.common)
        judge_log.append({"gold_id": g.uid, "pred_id": p.uid, "similarity": score, **result})
        if result["verdict"] == "same":
            matches.append(ActionMatch(g.uid, p.uid, "judge", g.canonical_label))
    return matches, judge_log


def compute_action_prf1(gold_count: int, pred_count: int, tp: int) -> dict:
    precision = tp / pred_count if pred_count else 0.0
    recall = tp / gold_count if gold_count else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "gold_count": gold_count, "pred_count": pred_count}


def score_action_matching(
    gold_steps: list[dict], pred_steps: list[dict], *, gold_core_actions_path: Path | None = None
) -> dict:
    """전체 진입점: canonicalize -> Rule Match -> Judge Match -> Action P/R/F1."""
    plain_map = load_action_equivalence_map()
    conditional_groups = load_conditional_equivalence_groups()
    include_map = load_gold_core_actions(gold_core_actions_path)

    gold_actions = flatten_scored_actions(gold_steps, include_map=include_map, plain_map=plain_map, conditional_groups=conditional_groups)
    pred_actions = flatten_scored_actions(pred_steps, include_map=None, plain_map=plain_map, conditional_groups=conditional_groups)

    rule_matches, remaining_gold, remaining_pred = pair_rule_matches(gold_actions, pred_actions)
    judge_matches, judge_log = pair_judge_matches(remaining_gold, remaining_pred)

    all_matches = rule_matches + judge_matches
    matched_g = {m.gold_id for m in all_matches}
    matched_p = {m.pred_id for m in all_matches}

    prf1 = compute_action_prf1(len(gold_actions), len(pred_actions), len(all_matches))

    return {
        "action_prf1": prf1,
        "matches": [m.__dict__ for m in all_matches],
        "rule_match_count": len(rule_matches),
        "judge_match_count": len(judge_matches),
        "unmatched_gold": [a.uid for a in gold_actions if a.uid not in matched_g],
        "unmatched_pred": [a.uid for a in pred_actions if a.uid not in matched_p],
        "judge_log": judge_log,
        "gold_actions_by_uid": {a.uid: a.__dict__ for a in gold_actions},
        "pred_actions_by_uid": {a.uid: a.__dict__ for a in pred_actions},
    }
