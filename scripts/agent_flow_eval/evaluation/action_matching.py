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
    is_ambiguous_generic_action,
    is_disabled_step,
    matches_infrastructure_keyword,
    should_exclude_action,
)
from attribute_signature import common_signature, readable_parameters  # noqa: E402
from judge import judge_action_equivalence, judge_core_business_relevance  # noqa: E402

CONFIRMED_GOLDSET_ROOT = EVAL_ROOT.parent / "goldset_expansion" / "confirmed_goldset"
CORE_BUSINESS_CACHE_PATH = EVAL_ROOT / "gold_core_actions" / "_llm_classification_cache.json"

# Filtering is defined in action_filters.py and applied symmetrically by both
# converters. This scorer repeats the same check only as a defensive boundary.
@dataclass
class ScoredAction:
    uid: str
    package: str | None
    action: str | None
    canonical_label: str
    common: dict = field(default_factory=dict)
    readable_params: str = ""

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


def flatten_scored_actions(
    steps: list[dict[str, Any]],
    *,
    plain_map: dict[str, str],
    conditional_groups: list[dict],
    in_catch: bool = False,
    _counter: list[int] | None = None,
    _branch_groups: list[dict] | None = None,
) -> list[ScoredAction]:
    """steps 트리에서 채점 대상 action을 순서대로 추출한다.

    disabled, 공통 변환 제외 action, catch 내부 action만 대칭적으로 제외한다.

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
    유일하게 만든다.

    주의 3: `_branch_groups`가 주어지면 `if` 스텝을 만날 때마다(중첩 depth
    상관없이) 그 if의 분기(if 자신의 top-level steps + branches의 각 elseIf/
    else)별로 어떤 uid가 속하는지 기록한다(score_branch_coverage용 사이드채널
    - 실제 0376 데이터로 확인함: if/elseIf/elseIf 세 분기가 상호배타적인데
    지금 이 함수의 반환값(`out`)에는 세 분기 액션이 전부 풀려서 하나의 flat
    리스트로 섞인다 - 그건 그대로 두고, 별도로 "이 uid가 어느 if의 어느
    분기 소속인지"만 추가로 남긴다). loop/trigger_loop/try는 "여러 대안 중
    하나"가 아니라 반복/에러처리라서 분기 그룹 대상이 아니다 - 그동안 하던
    pooling 그대로 유지."""
    if _counter is None:
        _counter = [0]
    if _branch_groups is None:
        _branch_groups = []
    out: list[ScoredAction] = []
    for step in steps:
        if is_disabled_step(step):
            continue
        step_type = step.get("type")
        if step_type == "action":
            package, action = step.get("package"), step.get("action")
            if should_exclude_action(package, action):
                continue
            if in_catch:
                continue
            raw_uid = step.get("uid") or f"idx_{_counter[0]}"
            unique_uid = f"{raw_uid}#{_counter[0]}"
            _counter[0] += 1
            common = common_signature(step)
            canonical = canonicalize_action(package, action, common, plain_map, conditional_groups)
            params = readable_parameters(step)
            out.append(ScoredAction(uid=unique_uid, package=package, action=action, canonical_label=canonical, common=common, readable_params=params))
            continue
        if step_type == "container":
            out.extend(flatten_scored_actions(step.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter, _branch_groups=_branch_groups))
        elif step_type == "if":
            branch_records: list[dict] = []

            if_body = flatten_scored_actions(step.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter, _branch_groups=_branch_groups)
            out.extend(if_body)
            branch_records.append({"branch_name": "if", "uids": [a.uid for a in if_body]})

            for branch in step.get("branches", []) or []:
                branch_actions = flatten_scored_actions(branch.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter, _branch_groups=_branch_groups)
                out.extend(branch_actions)
                branch_records.append({"branch_name": str(branch.get("branch") or "branch"), "uids": [a.uid for a in branch_actions]})

            _branch_groups.append({"group_id": f"if#{step.get('uid') or len(_branch_groups)}", "branches": branch_records})
        elif step_type in {"loop", "trigger_loop"}:
            out.extend(flatten_scored_actions(step.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter, _branch_groups=_branch_groups))
            for branch in step.get("branches", []) or []:
                out.extend(flatten_scored_actions(branch.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter, _branch_groups=_branch_groups))
        elif step_type == "try":
            out.extend(flatten_scored_actions(step.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=in_catch, _counter=_counter, _branch_groups=_branch_groups))
            for branch in step.get("branches", []) or []:
                branch_in_catch = in_catch or branch.get("branch") == "catch"
                out.extend(flatten_scored_actions(branch.get("steps", []) or [], plain_map=plain_map, conditional_groups=conditional_groups, in_catch=branch_in_catch, _counter=_counter, _branch_groups=_branch_groups))
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
    try:
        gold_vecs = _embed(gold_texts)
        pred_vecs = _embed(pred_texts)
    except Exception as exc:  # Judge is optional; confirmed Rule matches remain usable.
        return [], [{
            "status": "unavailable",
            "stage": "embedding",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }]

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
        try:
            result = judge_action_equivalence(g.raw_label, g.common, p.raw_label, p.common)
        except Exception as exc:  # Keep the pair unmatched and make the failure explicit.
            judge_log.append({
                "gold_id": g.uid,
                "pred_id": p.uid,
                "similarity": score,
                "status": "unavailable",
                "stage": "judge",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
            continue
        judge_log.append({"gold_id": g.uid, "pred_id": p.uid, "similarity": score, **result})
        if result["verdict"] == "same":
            matches.append(ActionMatch(g.uid, p.uid, "judge", g.canonical_label))
    return matches, judge_log


def load_business_definition(case_id: str | None) -> tuple[str | None, str | None]:
    """confirmed_goldset/briefs/<case_id>_*.md 에서 업무정의서 원문과 과제명을
    읽는다. case_id가 없거나 파일이 없으면 (None, None) - 핵심업무 분류는
    그냥 건너뛴다(모든 액션을 핵심업무로 취급, 기존 동작과 동일)."""
    if not case_id:
        return None, None
    matches = list(CONFIRMED_GOLDSET_ROOT.glob(f"briefs/{case_id}_*.md"))
    if not matches:
        return None, None
    text = matches[0].read_text(encoding="utf-8")
    title_match = None
    for line in text.splitlines():
        if line.startswith("과제명:"):
            title_match = line.split(":", 1)[1].strip()
            break
    return text, title_match


def _load_classification_cache() -> dict:
    if not CORE_BUSINESS_CACHE_PATH.exists():
        return {}
    return json.loads(CORE_BUSINESS_CACHE_PATH.read_text(encoding="utf-8"))


def _save_classification_cache(cache: dict) -> None:
    CORE_BUSINESS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORE_BUSINESS_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def classify_core_business_actions(
    actions: list[ScoredAction],
    *,
    case_id: str | None,
    business_definition: str | None,
    case_title: str | None,
) -> tuple[list[ScoredAction], list[ScoredAction], list[dict]]:
    """AMBIGUOUS_GENERIC_ACTIONS만 대상으로, (1) 키워드 규칙으로 확실한 것부터
    무료로 걸러내고 (2) 남은 것만 4o-mini(judge_core_business_relevance)에
    묻는다. 결과를 케이스 무관 공유 캐시에 저장해 재실행 시 재호출을 피한다.

    business_definition이 없으면(케이스가 confirmed_goldset 밖에 있는 경우,
    예: 금시세봇 ad-hoc 테스트) 분류 자체를 건너뛰고 전부 핵심업무로 취급한다
    - 무리해서 맥락 없이 판정하지 않는다.

    주의(2026-08-03) - HANDOFF_CODEX.md가 "공식 채점에서 쓰지 않는다"고 명시한
    `gold_core_actions/<id>.json` 수동 UID 목록과 이 함수는 **다른 접근**이다.
    그 파일들은 Gold에만 적용되는 비대칭 보정이라 예측 쪽은 그대로 둔 채 Gold
    분모만 줄어서 점수를 부당하게 올릴 위험이 있어 반려됐다(공식 경로에서
    제거 확정). 이 함수는 호출부(action_matching.py의 score_action_matching,
    run_eval_case.py, audit_final_goldset.py)에서 항상 gold_actions_all과
    pred_actions_all 양쪽에 대칭적으로 적용되고, 실측으로도 예측 쪽 제외가
    실제로 발생함을 확인함(예: 0140/0085/0376/0419 예측에서 1개씩 제외) -
    "Gold만 봐주는" 구조가 아니다. `gold_core_actions/` 디렉터리 자체는 이
    함수와 무관한 별도의 수동 참고자료로 남아 있을 뿐, 이 함수가 그 파일을
    읽거나 쓰지 않는다."""
    if not business_definition:
        return list(actions), [], []

    cache = _load_classification_cache()
    cache_dirty = False
    core: list[ScoredAction] = []
    excluded: list[ScoredAction] = []
    log: list[dict] = []

    for a in actions:
        if not is_ambiguous_generic_action(a.package, a.action):
            core.append(a)
            continue
        if matches_infrastructure_keyword(a.readable_params):
            excluded.append(a)
            log.append({"uid": a.uid, "label": a.raw_label, "params": a.readable_params, "verdict": "not_core_business", "source": "rule"})
            continue

        cache_key = f"{case_id or ''}|{a.raw_label}|{a.readable_params}"
        cached = cache.get(cache_key)
        if cached is None:
            try:
                result = judge_core_business_relevance(a.raw_label, a.readable_params, business_definition, case_title or "")
                cached = {"verdict": result["verdict"], "reason": result["reason"]}
            except Exception as exc:  # LLM 판정 불가 - 핵심업무로 남겨 잘못 빼지 않는다
                cached = {"verdict": "core_business", "reason": f"classification_unavailable: {type(exc).__name__}"}
            cache[cache_key] = cached
            cache_dirty = True

        log.append({"uid": a.uid, "label": a.raw_label, "params": a.readable_params, "verdict": cached["verdict"], "reason": cached.get("reason"), "source": "llm"})
        if cached["verdict"] == "not_core_business":
            excluded.append(a)
        else:
            core.append(a)

    if cache_dirty:
        _save_classification_cache(cache)
    return core, excluded, log


def compute_action_prf1(gold_count: int, pred_count: int, tp: int) -> dict:
    precision = tp / pred_count if pred_count else 0.0
    recall = tp / gold_count if gold_count else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "gold_count": gold_count, "pred_count": pred_count}


def score_branch_coverage(
    branch_groups: list[dict], matched_gold_uids: set[str], core_gold_uids: set[str]
) -> dict:
    """if/elseIf/else 상호배타적 분기가 flatten 시 한 리스트로 풀리면서 생기는
    "중복 카운트" 문제(실측: 0376의 if/elseIf/elseIf 3분기, 각 분기 copyFiles+
    deleteFiles가 거의 동일해서 9개가 다 gold_count에 잡힘)에 대한 진단용
    별도 지표다. 메인 Action P/R/F1/Action Chain은 그대로 두고(이미 검증된
    로직), gold의 if 분기 구조 위에 이미 확정된 매칭 결과(rule+judge)를
    재사용해서 재집계만 한다 - 새 분기-대-분기 매칭 알고리즘은 만들지 않는다
    (2026-08-03 사용자 결정: "매칭 알고리즘 없이, 액션 매칭 결과 재사용").

    - Branch Coverage: 분기의 핵심 액션이 "전부" 매칭됐는지 all-or-nothing으로
      판단해 (전부 매칭된 분기 수) / (전체 분기 수)로 집계한다. 절반 이상처럼
      임의 임계값은 넣지 않는다 - 근거 없는 숫자를 만들지 않기 위함(사용자
      원칙: 억지로 점수를 만들지 말 것).
    - Branch Score: 분기별 (매칭된 핵심 액션 수 / 그 분기 핵심 액션 수)의
      평균이다 - 부분 구현에도 연속적인 점수를 준다.

    핵심업무 분류(classify_core_business_actions)에서 제외된 액션은 애초에
    분기의 "핵심 액션"이 아니므로 core_gold_uids로 걸러낸다. 분기에 핵심
    액션이 하나도 없으면(로그/메시지뿐이거나 전부 제외됨) coverage_ratio를
    None으로 남기고 분모(scoreable_branch_count)에서 제외한다 - 핵심 액션이
    원래 없는 분기를 강제로 0점 처리하지 않기 위함."""
    branch_reports: list[dict] = []
    branch_ratios: list[float] = []
    fully_covered = 0
    scoreable_branch_count = 0

    for group in branch_groups:
        group_branch_reports = []
        for branch in group["branches"]:
            core_branch_uids = [u for u in branch["uids"] if u in core_gold_uids]
            if not core_branch_uids:
                group_branch_reports.append(
                    {"branch_name": branch["branch_name"], "core_action_count": 0, "matched_count": 0, "coverage_ratio": None}
                )
                continue
            matched_count = sum(1 for u in core_branch_uids if u in matched_gold_uids)
            ratio = matched_count / len(core_branch_uids)
            branch_ratios.append(ratio)
            scoreable_branch_count += 1
            if matched_count == len(core_branch_uids):
                fully_covered += 1
            group_branch_reports.append(
                {"branch_name": branch["branch_name"], "core_action_count": len(core_branch_uids), "matched_count": matched_count, "coverage_ratio": ratio}
            )
        branch_reports.append({"group_id": group["group_id"], "branches": group_branch_reports})

    return {
        "applicable": scoreable_branch_count > 0,
        "group_count": len(branch_groups),
        "scoreable_branch_count": scoreable_branch_count,
        "branch_coverage": (fully_covered / scoreable_branch_count) if scoreable_branch_count else None,
        "branch_score": (sum(branch_ratios) / len(branch_ratios)) if branch_ratios else None,
        "groups": branch_reports,
    }


def score_action_matching(gold_steps: list[dict], pred_steps: list[dict], *, case_id: str | None = None) -> dict:
    """전체 진입점: canonicalize -> 핵심업무 분류(규칙+LLM) -> Rule Match ->
    Judge Match -> Action P/R/F1.

    case_id가 주어지고 confirmed_goldset/briefs/에 해당 업무정의서가 있으면
    핵심업무 분류를 gold/pred 양쪽에 대칭 적용한다. 없으면 분류를 건너뛰고
    기존과 동일하게 전부 핵심업무로 취급한다(하위 호환)."""
    plain_map = load_action_equivalence_map()
    conditional_groups = load_conditional_equivalence_groups()
    gold_branch_groups: list[dict] = []
    gold_actions_all = flatten_scored_actions(gold_steps, plain_map=plain_map, conditional_groups=conditional_groups, _branch_groups=gold_branch_groups)
    pred_actions_all = flatten_scored_actions(pred_steps, plain_map=plain_map, conditional_groups=conditional_groups)

    business_definition, case_title = load_business_definition(case_id)
    gold_actions, gold_excluded, gold_classification_log = classify_core_business_actions(
        gold_actions_all, case_id=case_id, business_definition=business_definition, case_title=case_title
    )
    pred_actions, pred_excluded, pred_classification_log = classify_core_business_actions(
        pred_actions_all, case_id=case_id, business_definition=business_definition, case_title=case_title
    )

    rule_matches, remaining_gold, remaining_pred = pair_rule_matches(gold_actions, pred_actions)
    judge_matches, judge_log = pair_judge_matches(remaining_gold, remaining_pred)

    all_matches = rule_matches + judge_matches
    matched_g = {m.gold_id for m in all_matches}
    matched_p = {m.pred_id for m in all_matches}

    prf1 = compute_action_prf1(len(gold_actions), len(pred_actions), len(all_matches))
    core_gold_uids = {a.uid for a in gold_actions}
    branch_coverage = score_branch_coverage(gold_branch_groups, matched_g, core_gold_uids)

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
        "branch_coverage": branch_coverage,
        "core_business_classification": {
            "gold_excluded_count": len(gold_excluded),
            "pred_excluded_count": len(pred_excluded),
            "gold_excluded": [a.uid for a in gold_excluded],
            "pred_excluded": [a.uid for a in pred_excluded],
            "gold_log": gold_classification_log,
            "pred_log": pred_classification_log,
        },
    }
