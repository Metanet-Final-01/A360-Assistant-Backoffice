from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from action_filters import (
    action_label,
    is_control_flow_marker_action,
    is_disabled_step,
    is_formatting_only_action,
    is_session_lifecycle_action,
)
from action_chain import compute_action_chain
from action_matching import NOISE_PACKAGES, flatten_scored_actions, load_action_equivalence_map as load_action_matching_equivalence_map, load_conditional_equivalence_groups, load_gold_core_actions, normalize_action_label, pair_judge_matches, pair_rule_matches
from adapters.worfbench_adapter import score_worfbench, score_worfbench_f1chain
from path_utils import ensure_child_path, safe_path_component

# PM4Py(score_pm4py_conformance/compare_pm4py_artifacts)는 재설계(2026-07-30,
# peaceful-watching-possum.md)로 액티브 리포트에서 제외됐다 - 사람이 고른 구현 1개 vs
# 에이전트가 고른 다른 구현을 비교하는 구조상 안 맞고(정상 구현차이를 전부 deviation으로
# 잡음). PM4Py 코드 자체(adapters/pm4py_adapter.py)는 남겨뒀다 - 필요해지면 다시 쓸 수
# 있다. `core_task.py`(CORE_PACKAGE_KEYS 등)와 이 파일의 옛 package_family()/
# salient_families()는 근거 문서 없는 하드코딩 패키지 분류였음(git log로 도입 커밋에
# 근거 설명 없음을 확인함) - **파일째 완전히 삭제됨**(남겨두지 않음), 재도입하지 말 것.
# 대신 action_matching.py(Rule/Judge Match 기반 Action P/R/F1)와 action_chain.py
# (LIS 기반 Action Chain F1)를 새로 쓴다.


@dataclass(frozen=True)
class Paths:
    root: Path
    case_id: str
    run_id: str
    gold_normalized: Path
    pred_normalized: Path
    gold_pm4py_dir: Path
    pred_pm4py_dir: Path
    gold_worfbench: Path
    pred_worfbench: Path
    report_dir: Path


def goldset_root() -> Path:
    return Path(__file__).resolve().parents[1]


def only_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected exactly one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def resolve_paths(case_id: str, run_id: str) -> Paths:
    root = goldset_root()
    safe_case_id = safe_path_component(case_id, field="case_id")
    safe_run_id = safe_path_component(run_id, field="run_id")
    eval_root = root / "eval_inputs"
    runner_logs_root = root / "runner" / "logs"
    reports_root = root / "evaluation" / "reports"
    gold_norm_dir = ensure_child_path(eval_root, eval_root / "normalized_workflows_13" / safe_case_id, field="case_id")
    pred_run_dir = ensure_child_path(runner_logs_root, runner_logs_root / safe_run_id, field="run_id")
    pred_norm_dir = pred_run_dir / "converted_recommendation" / "normalized"
    gold_worf_dir = ensure_child_path(eval_root, eval_root / "worfbench_13" / safe_case_id, field="case_id")
    pred_worf_dir = pred_run_dir / "converted_recommendation" / "worfbench"
    report_dir = ensure_child_path(reports_root, reports_root / safe_run_id / safe_case_id, field="report_dir")

    paths = Paths(
        root=root,
        case_id=safe_case_id,
        run_id=safe_run_id,
        gold_normalized=only_file(gold_norm_dir, "*.goldset.json"),
        pred_normalized=only_file(pred_norm_dir, "*.goldset.json"),
        gold_pm4py_dir=ensure_child_path(eval_root, eval_root / "pm4py_13" / safe_case_id, field="case_id"),
        pred_pm4py_dir=pred_run_dir / "converted_recommendation" / "pm4py",
        gold_worfbench=only_file(gold_worf_dir, "*.worfbench.json"),
        pred_worfbench=only_file(pred_worf_dir, "*.worfbench.json"),
        report_dir=report_dir,
    )

    for path in [
        paths.gold_normalized,
        paths.pred_normalized,
        paths.gold_pm4py_dir,
        paths.pred_pm4py_dir,
        paths.gold_worfbench,
        paths.pred_worfbench,
    ]:
        if not path.exists():
            raise FileNotFoundError(f"Missing required evaluation input: {path}")
    return paths


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_action_equivalence_map(root: Path) -> dict[str, str]:
    path = root / "evaluation" / "action_equivalence_rules.json"
    payload = load_json(path)
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


def canonicalize_actions(actions: list[str], mapping: dict[str, str]) -> list[str]:
    return [mapping.get(normalize_action_label(action), action) for action in actions]


def flatten_actions(steps: list[dict[str, Any]], excluded: list[str] | None = None) -> list[str]:
    actions: list[str] = []
    for step in steps:
        if is_disabled_step(step):
            if excluded is not None:
                excluded.append(action_label(step.get("package"), step.get("action")) or step.get("type", "disabled"))
            continue
        step_type = step.get("type")
        if step_type == "action":
            package = step.get("package")
            action = step.get("action")
            label = action_label(package, action)
            if (
                is_session_lifecycle_action(package, action)
                or is_control_flow_marker_action(package, action)
                or is_formatting_only_action(package, action)
                or package in NOISE_PACKAGES
            ):
                if excluded is not None:
                    excluded.append(label)
                continue
            actions.append(label)
        elif step_type == "container":
            actions.extend(flatten_actions(step.get("steps", []) or [], excluded))
        elif step_type in {"if", "loop", "trigger_loop"}:
            actions.extend(flatten_actions(step.get("steps", []) or [], excluded))
            for branch in step.get("branches", []) or []:
                actions.extend(flatten_actions(branch.get("steps", []) or [], excluded))
        elif step_type == "try":
            actions.extend(flatten_actions(step.get("steps", []) or [], excluded))
            for branch in step.get("branches", []) or []:
                if branch.get("branch") == "finally":
                    actions.extend(flatten_actions(branch.get("steps", []) or [], excluded))
        else:
            raise ValueError(f"Unknown step type: {step_type!r}")
    return actions


def packages(actions: list[str]) -> list[str]:
    return [action.split(".", 1)[0] for action in actions]

# package_family()/package_families()/salient_families() 삭제됨(2026-07-30) - 근거
# 문서 없는 하드코딩 패키지 분류였음(git log로 확인, 도입 커밋에 근거 설명 없음).
# core_task.py의 CORE_PACKAGE_KEYS와도 File/Folder/XML/JSONHandler/Dictionary/List를
# 놓고 서로 모순됐던 것 - action_matching.py/action_chain.py로 대체.


def adjacent_edges(actions: list[str]) -> list[tuple[str, str]]:
    if not actions:
        return []
    return [("START", actions[0])] + list(zip(actions, actions[1:])) + [(actions[-1], "END")]


def lcs_len(a: list[str], b: list[str]) -> int:
    previous = [0] * (len(b) + 1)
    for left in a:
        current = [0]
        for idx, right in enumerate(b, start=1):
            current.append(previous[idx - 1] + 1 if left == right else max(previous[idx], current[-1]))
        previous = current
    return previous[-1]


def multiset_score(gold_items: list[Any], pred_items: list[Any]) -> dict[str, Any]:
    gold = Counter(gold_items)
    pred = Counter(pred_items)
    overlap = sum((gold & pred).values())
    precision = overlap / sum(pred.values()) if pred else 0.0
    recall = overlap / sum(gold.values()) if gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "overlap": overlap,
        "gold_count": sum(gold.values()),
        "prediction_count": sum(pred.values()),
    }


def sequence_score(gold_actions: list[str], pred_actions: list[str]) -> dict[str, Any]:
    common = lcs_len(gold_actions, pred_actions)
    precision = common / len(pred_actions) if pred_actions else 0.0
    recall = common / len(gold_actions) if gold_actions else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "lcs": common,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gold_count": len(gold_actions),
        "prediction_count": len(pred_actions),
    }


def first_mismatches(gold_actions: list[str], pred_actions: list[str], limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(max(len(gold_actions), len(pred_actions))):
        gold = gold_actions[idx] if idx < len(gold_actions) else None
        pred = pred_actions[idx] if idx < len(pred_actions) else None
        if gold != pred:
            rows.append({"index": idx + 1, "gold": gold, "prediction": pred})
        if len(rows) >= limit:
            break
    return rows


def score_normalized(gold_path: Path, pred_path: Path, *, gold_core_actions_path: Path | None = None) -> dict[str, Any]:
    gold = load_json(gold_path)
    pred = load_json(pred_path)
    equivalence_map = load_action_equivalence_map(goldset_root())
    excluded_gold_actions: list[str] = []
    excluded_prediction_actions: list[str] = []
    gold_actions = flatten_actions(gold.get("steps", []) or [], excluded_gold_actions)
    pred_actions = flatten_actions(pred.get("steps", []) or [], excluded_prediction_actions)
    gold_canonical_actions = canonicalize_actions(gold_actions, equivalence_map)
    pred_canonical_actions = canonicalize_actions(pred_actions, equivalence_map)

    # action_matching.py/action_chain.py - Rule/Judge Match 기반 Action P/R/F1 +
    # Action Chain F1 (재설계 v4, peaceful-watching-possum.md 참고).
    am_plain_map = load_action_matching_equivalence_map()
    am_conditional = load_conditional_equivalence_groups()
    am_include_map = load_gold_core_actions(gold_core_actions_path)
    scored_gold = flatten_scored_actions(gold.get("steps", []) or [], include_map=am_include_map, plain_map=am_plain_map, conditional_groups=am_conditional)
    scored_pred = flatten_scored_actions(pred.get("steps", []) or [], include_map=None, plain_map=am_plain_map, conditional_groups=am_conditional)
    rule_matches, remaining_gold, remaining_pred = pair_rule_matches(scored_gold, scored_pred)
    judge_matches, judge_log = pair_judge_matches(remaining_gold, remaining_pred)
    all_matches = rule_matches + judge_matches
    action_chain = compute_action_chain(scored_gold, scored_pred, all_matches)
    tp = len(all_matches)
    action_precision = tp / len(scored_pred) if scored_pred else 0.0
    action_recall = tp / len(scored_gold) if scored_gold else 0.0
    action_f1 = (2 * action_precision * action_recall / (action_precision + action_recall)) if (action_precision + action_recall) else 0.0

    return {
        "gold_path": str(gold_path),
        "prediction_path": str(pred_path),
        "gold_source_file": gold.get("source_file"),
        "prediction_source_file": pred.get("source_file"),
        "preprocessing": {
            "excluded_rule": "session_lifecycle_or_control_flow_marker_or_formatting_or_noise_action",
            "excluded_regex": {
                "package": "^(web\\s*automation|webautomation|browser|recorder)$",
                "action": "session",
            },
            "excluded_gold_actions": excluded_gold_actions,
            "excluded_prediction_actions": excluded_prediction_actions,
            "excluded_gold_count": len(excluded_gold_actions),
            "excluded_prediction_count": len(excluded_prediction_actions),
            "action_equivalence_rules_path": str(goldset_root() / "evaluation" / "action_equivalence_rules.json"),
            "action_equivalence_member_count": len(equivalence_map),
            "gold_core_actions_path": str(gold_core_actions_path) if gold_core_actions_path else None,
        },
        "gold_action_count": len(gold_actions),
        "prediction_action_count": len(pred_actions),
        "action_sequence": sequence_score(gold_actions, pred_actions),
        "action_multiset": multiset_score(gold_actions, pred_actions),
        "canonical_action_sequence": sequence_score(gold_canonical_actions, pred_canonical_actions),
        "canonical_action_multiset": multiset_score(gold_canonical_actions, pred_canonical_actions),
        "package_multiset": multiset_score(packages(gold_actions), packages(pred_actions)),
        "adjacent_edge_multiset": multiset_score(adjacent_edges(gold_actions), adjacent_edges(pred_actions)),
        "canonical_adjacent_edge_multiset": multiset_score(adjacent_edges(gold_canonical_actions), adjacent_edges(pred_canonical_actions)),
        "first_mismatches": first_mismatches(gold_actions, pred_actions),
        "first_canonical_mismatches": first_mismatches(gold_canonical_actions, pred_canonical_actions),
        "gold_actions_preview": gold_actions[:20],
        "gold_canonical_actions_preview": gold_canonical_actions[:20],
        "prediction_actions": pred_actions,
        "prediction_canonical_actions": pred_canonical_actions,
        # 1차 확정 지표(§2, §4 - action_matching.py/action_chain.py)
        "action_prf1": {"precision": action_precision, "recall": action_recall, "f1": action_f1, "tp": tp, "gold_count": len(scored_gold), "pred_count": len(scored_pred)},
        "action_chain": action_chain,
        "rule_match_count": len(rule_matches),
        "judge_match_count": len(judge_matches),
        "judge_log": judge_log,
        "unmatched_gold_uids": [a.uid for a in scored_gold if a.uid not in {m.gold_id for m in all_matches}],
        "unmatched_pred_uids": [a.uid for a in scored_pred if a.uid not in {m.pred_id for m in all_matches}],
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    normalized = payload["normalized"]
    lines = [
        "# Evaluation Report",
        "",
        f"- Run: `{payload['run_id']}`",
        f"- Case: `{payload['case_id']}`",
        f"- Created at: `{payload['created_at']}`",
        f"- Gold actions: `{normalized['gold_action_count']}`",
        f"- Prediction actions: `{normalized['prediction_action_count']}`",
        "",
        "## Scores (1차 확정 지표 - action_matching.py/action_chain.py)",
        "",
        f"- **Action Precision/Recall/F1**: `{normalized['action_prf1']['precision']:.4f}` / "
        f"`{normalized['action_prf1']['recall']:.4f}` / `{normalized['action_prf1']['f1']:.4f}` "
        f"(rule match `{normalized['rule_match_count']}`, judge match `{normalized['judge_match_count']}`)",
        f"- **Action Chain Precision/Recall/F1** (액션 선택+상대 순서를 함께 반영, 순서 정확도만은 아님): "
        f"`{normalized['action_chain']['precision']:.4f}` / `{normalized['action_chain']['recall']:.4f}` / "
        f"`{normalized['action_chain']['f1']:.4f}`",
        "",
        "## 참고 지표 (레거시 - package.action 완전일치/LCS 기반, 구현 차이를 구분 못함)",
        "",
        f"- Action sequence LCS F1: `{normalized['action_sequence']['f1']:.4f}` "
        f"(precision `{normalized['action_sequence']['precision']:.4f}`, recall `{normalized['action_sequence']['recall']:.4f}`)",
        f"- Action multiset F1: `{normalized['action_multiset']['f1']:.4f}`",
        f"- Canonical action sequence LCS F1: `{normalized['canonical_action_sequence']['f1']:.4f}`",
        f"- Canonical action multiset F1: `{normalized['canonical_action_multiset']['f1']:.4f}`",
        f"- Package multiset F1: `{normalized['package_multiset']['f1']:.4f}`",
        f"- Adjacent edge F1: `{normalized['adjacent_edge_multiset']['f1']:.4f}`",
        "",
        "## 외부 벤치마크 비교용 (WorFEval, 원본 라이브러리 그대로 - all-mpnet-base-v2, 임계값 0.6)",
        "",
        f"- WorFEval Chain precision: `{payload['worfbench'].get('precision')}`",
        f"- WorFEval Chain recall: `{payload['worfbench'].get('recall')}`",
        f"- WorFEval Chain F1: `{payload['worfbench'].get('f1_score')}`",
        f"- Diagnostic WorFBench node-label F1: `{payload['worfbench_diagnostic_artifact_f1']['node_label_f1']['f1']:.4f}`",
        f"- Diagnostic WorFBench edge F1: `{payload['worfbench_diagnostic_artifact_f1']['edge_f1']['f1']:.4f}`",
        "",
        "## First Mismatches",
        "",
    ]
    if normalized["first_mismatches"]:
        for row in normalized["first_mismatches"]:
            lines.append(f"- `{row['index']}` gold=`{row['gold']}` prediction=`{row['prediction']}`")
    else:
        lines.append("- No positional mismatches.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate one backend runner output against one 13-case goldset artifact.")
    parser.add_argument("--case-id", default="03_0131_currency-rate---oanda")
    parser.add_argument("--run-id", default="runner_v2_repeat_20260715_01")
    parser.add_argument("--gold-core-actions", type=Path, default=None, help="gold_core_actions/<id>.json (§7 - uid별 include/exclude 고정 파일)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = resolve_paths(args.case_id, args.run_id)
    paths.report_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_id": paths.case_id,
        "run_id": paths.run_id,
        "normalized": score_normalized(paths.gold_normalized, paths.pred_normalized, gold_core_actions_path=args.gold_core_actions),
        # WorFEval(원본 벤더 라이브러리, all-mpnet-base-v2 임베딩+임계값0.6) - "외부
        # 벤치마크 비교용"으로만 유지. PM4Py는 재설계로 액티브 리포트에서 완전히 뺌
        # (adapters/pm4py_adapter.py 자체는 남아있음, 필요해지면 다시 부르면 됨).
        "worfbench": score_worfbench_f1chain(paths.gold_normalized, paths.pred_normalized),
        "worfbench_diagnostic_artifact_f1": score_worfbench(paths.gold_worfbench, paths.pred_worfbench),
    }

    json_path = paths.report_dir / "evaluation.json"
    md_path = paths.report_dir / "evaluation.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, payload)
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(md_path),
                "action_precision": payload["normalized"]["action_prf1"]["precision"],
                "action_recall": payload["normalized"]["action_prf1"]["recall"],
                "action_f1": payload["normalized"]["action_prf1"]["f1"],
                "action_chain_precision": payload["normalized"]["action_chain"]["precision"],
                "action_chain_recall": payload["normalized"]["action_chain"]["recall"],
                "action_chain_f1": payload["normalized"]["action_chain"]["f1"],
                "action_sequence_f1": payload["normalized"]["action_sequence"]["f1"],
                "action_multiset_f1": payload["normalized"]["action_multiset"]["f1"],
                "canonical_action_sequence_f1": payload["normalized"]["canonical_action_sequence"]["f1"],
                "canonical_action_multiset_f1": payload["normalized"]["canonical_action_multiset"]["f1"],
                "package_multiset_f1": payload["normalized"]["package_multiset"]["f1"],
                "worfbench_precision_external_reference": payload["worfbench"].get("precision"),
                "worfbench_recall_external_reference": payload["worfbench"].get("recall"),
                "worfbench_f1_external_reference": payload["worfbench"].get("f1_score"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
