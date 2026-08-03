from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from action_chain import compute_action_chain
from action_matching import (
    classify_core_business_actions,
    compute_action_prf1,
    flatten_scored_actions,
    load_action_equivalence_map,
    load_business_definition,
    load_conditional_equivalence_groups,
    pair_judge_matches,
    pair_rule_matches,
    score_branch_coverage,
)
from action_filters import normalize_steps_for_evaluation
from adapters.worfbench_adapter import score_worfbench_f1chain


EVAL_DIR = Path(__file__).resolve().parent
AGENT_FLOW_ROOT = EVAL_DIR.parent
RUN_LOGS = AGENT_FLOW_ROOT / "runner" / "logs"
GOLD_DIR = AGENT_FLOW_ROOT / "goldset_expansion" / "confirmed_goldset" / "gold"
FINAL_CASE_IDS = {"0085", "0089", "0098", "0112", "0131", "0140", "0164", "0376", "0419"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def only_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def case_id_from_run(run: dict[str, Any]) -> str:
    case_id = Path(run["pdf"]).name[:4]
    if case_id not in FINAL_CASE_IDS:
        raise ValueError(f"Unexpected final-goldset case ID: {case_id}")
    return case_id


def load_runs(
    batch_manifest: Path,
    overrides: dict[str, str] | None = None,
    *,
    require_all: bool = True,
) -> tuple[dict[str, str], list[str]]:
    payload = load_json(batch_manifest)
    runs: dict[str, str] = {}
    recovered_artifacts: list[str] = []
    for run in payload["runs"]:
        case_id = case_id_from_run(run)
        run_id = run["run_id"]
        completed = run.get("runner_status") == "ok" and run.get("converter_status") == "ok"
        artifact_exists = has_complete_run_artifact(run_id)
        if completed or artifact_exists:
            runs[case_id] = run_id
        if not completed and artifact_exists:
            recovered_artifacts.append(case_id)
    runs.update(overrides or {})
    missing = FINAL_CASE_IDS - runs.keys()
    if require_all and missing:
        raise ValueError(f"Successful converted runs missing for: {sorted(missing)}")
    return runs, sorted(recovered_artifacts)


def parse_run_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        case_id, separator, run_id = value.partition("=")
        if separator != "=" or case_id not in FINAL_CASE_IDS or not run_id:
            raise ValueError(f"Expected CASE_ID=RUN_ID for a final case, got: {value}")
        overrides[case_id] = run_id
    return overrides


def gold_path(case_id: str) -> Path:
    return only_file(GOLD_DIR, f"{case_id}_*.goldset.json")


def prediction_path(run_id: str) -> Path:
    return only_file(RUN_LOGS / run_id / "converted_recommendation" / "normalized", "*.goldset.json")


def has_complete_run_artifact(run_id: str) -> bool:
    run_dir = RUN_LOGS / run_id
    artifacts = list((run_dir / "converted_recommendation" / "normalized").glob("*.goldset.json"))
    manifest_path = run_dir / "run_manifest.json"
    if len(artifacts) != 1 or not manifest_path.exists():
        return False
    steps = load_json(manifest_path).get("steps", [])
    return bool(steps) and all(step.get("status") == "ok" for step in steps)


def control_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()

    def visit(rows: list[dict[str, Any]]) -> None:
        for step in rows:
            step_type = step.get("type")
            if step_type in {"if", "loop", "trigger_loop", "try"}:
                counts[step_type] += 1
            visit(step.get("steps", []) or [])
            for branch in step.get("branches", []) or []:
                visit(branch.get("steps", []) or [])

    visit(steps)
    return {name: counts[name] for name in ("if", "loop", "trigger_loop", "try")}


def score_case(version: str, case_id: str, run_id: str) -> dict[str, Any]:
    gold_file = gold_path(case_id)
    pred_file = prediction_path(run_id)
    gold = load_json(gold_file)
    pred = load_json(pred_file)
    gold_steps = normalize_steps_for_evaluation(gold.get("steps", []) or [])
    pred_steps = normalize_steps_for_evaluation(pred.get("steps", []) or [])

    plain_map = load_action_equivalence_map()
    conditional = load_conditional_equivalence_groups()
    gold_branch_groups: list[dict[str, Any]] = []
    gold_actions_all = flatten_scored_actions(
        gold_steps,
        plain_map=plain_map,
        conditional_groups=conditional,
        _branch_groups=gold_branch_groups,
    )
    pred_actions_all = flatten_scored_actions(
        pred_steps,
        plain_map=plain_map,
        conditional_groups=conditional,
    )

    # 핵심업무 분류(규칙+LLM, 2026-08-03) - case_id가 confirmed_goldset/briefs/에
    # 있는 업무정의서와 매칭되면(FINAL_CASE_IDS 전부 해당) gold/pred 양쪽에
    # 대칭 적용한다. 이후 rule_only든 judge-assisted든 전부 같은 필터링된
    # 액션 집합 위에서 계산된다 - "이 액션이 채점 대상인가"는 rule/judge 매칭
    # 방식과 무관한 선행 단계이기 때문.
    business_definition, case_title = load_business_definition(case_id)
    gold_actions, gold_excluded, gold_classification_log = classify_core_business_actions(
        gold_actions_all, case_id=case_id, business_definition=business_definition, case_title=case_title
    )
    pred_actions, pred_excluded, pred_classification_log = classify_core_business_actions(
        pred_actions_all, case_id=case_id, business_definition=business_definition, case_title=case_title
    )

    rule_matches, remaining_gold, remaining_pred = pair_rule_matches(gold_actions, pred_actions)
    judge_matches, judge_log = pair_judge_matches(remaining_gold, remaining_pred)
    matches = rule_matches + judge_matches
    matched_gold = {match.gold_id for match in matches}
    matched_pred = {match.pred_id for match in matches}
    gold_by_uid = {action.uid: action for action in gold_actions}
    pred_by_uid = {action.uid: action for action in pred_actions}
    core_gold_uids = {a.uid for a in gold_actions}
    branch_coverage = score_branch_coverage(gold_branch_groups, matched_gold, core_gold_uids)

    # 외부 벤치마크 비교용(원본 WorFEval t_eval_nodes, all-mpnet-base-v2 임베딩+
    # 임계값 0.6, 실제 networkx max-weight-matching) - action_prf1/action_chain과
    # 완전히 별개 경로다. 원본 gold/pred goldset.json(핵심업무 분류 전 원본)을
    # 그대로 넘긴다 - WorFEval은 원본 벤치마크 그대로 재현하는 게 목적이라 우리
    # 핵심업무 분류나 action_equivalence_rules_conditional.json을 적용하지 않는다.
    #
    # 방어적으로 try/except로도 감싼다(Qodo 리뷰 지적, 2026-08-03) - 근본 원인은
    # worfbench_adapter.py의 _import_worfbench()를 자체 try/except 안으로
    # 옮겨서 고쳤지만, WorFBench는 어디까지나 참고 지표라 이 호출부에서도
    # 한 번 더 막아 실제 주 지표(action_prf1/action_chain) 산출이 절대
    # 영향받지 않게 한다.
    try:
        worfbench = score_worfbench_f1chain(gold_file, pred_file)
    except Exception as exc:  # WorFBench는 참고 지표 - 실패해도 감사 전체를 막지 않는다
        worfbench = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}

    return {
        "case_id": case_id,
        "version": version,
        "run_id": run_id,
        "gold_path": str(gold_file),
        "prediction_path": str(pred_file),
        "normalization_policy": "shared_rule_based_converter",
        "rule_only_action_prf1": compute_action_prf1(
            len(gold_actions), len(pred_actions), len(rule_matches)
        ),
        "rule_only_action_chain": compute_action_chain(
            gold_actions, pred_actions, rule_matches
        ),
        "action_prf1": compute_action_prf1(len(gold_actions), len(pred_actions), len(matches)),
        "action_chain": compute_action_chain(gold_actions, pred_actions, matches),
        # 진단용 별도 지표 - if/elseIf/else 상호배타적 분기 중복 카운트 문제에
        # 대한 대응(0376 실측). 메인 action_prf1/action_chain에는 반영 안 됨.
        "branch_coverage": branch_coverage,
        "worfbench": worfbench,
        "core_business_classification": {
            "gold_excluded_count": len(gold_excluded),
            "pred_excluded_count": len(pred_excluded),
            "gold_excluded": [
                {"uid": a.uid, "label": a.raw_label, "params": a.readable_params} for a in gold_excluded
            ],
            "pred_excluded": [
                {"uid": a.uid, "label": a.raw_label, "params": a.readable_params} for a in pred_excluded
            ],
            "gold_log": gold_classification_log,
            "pred_log": pred_classification_log,
        },
        "control_counts": {
            "gold": control_counts(gold_steps),
            "prediction": control_counts(pred_steps),
        },
        "matches": [
            {
                **match.__dict__,
                "gold_label": gold_by_uid[match.gold_id].raw_label,
                "prediction_label": pred_by_uid[match.pred_id].raw_label,
            }
            for match in matches
        ],
        "unmatched_gold": [
            {"uid": action.uid, "label": action.raw_label, "canonical_label": action.canonical_label}
            for action in gold_actions
            if action.uid not in matched_gold
        ],
        "unmatched_prediction": [
            {"uid": action.uid, "label": action.raw_label, "canonical_label": action.canonical_label}
            for action in pred_actions
            if action.uid not in matched_pred
        ],
        "judge_log": judge_log,
    }


def judge_status(row: dict[str, Any]) -> str:
    statuses = {item.get("status") for item in row["judge_log"]}
    if "unavailable" in statuses:
        return "unavailable"
    if any(match["match_type"] == "judge" for match in row["matches"]):
        return "used"
    return "not_needed"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "version",
        "run_id",
        "gold_count",
        "pred_count",
        # 규칙기반(Rule Match)만 - LLM 호출 없이 재현 가능한 기준선
        "rule_only_tp",
        "rule_only_action_precision",
        "rule_only_action_recall",
        "rule_only_action_f1",
        "rule_only_chain_precision",
        "rule_only_chain_recall",
        "rule_only_chain_f1",
        # Rule Match + Judge Match(LLM 판정 포함) - 실행 간 소폭 변동 가능
        "tp",
        "action_precision",
        "action_recall",
        "action_f1",
        "chain_precision",
        "chain_recall",
        "chain_f1",
        "judge_status",
        "unmatched_gold_count",
        "unmatched_prediction_count",
        "gold_excluded_count",
        "pred_excluded_count",
        "branch_coverage",
        "branch_score",
        "branch_group_count",
        # 외부 벤치마크 비교용(원본 WorFEval t_eval_nodes, 별도 경로 - 참고용)
        "worfbench_status_external_reference",
        "worfbench_precision_external_reference",
        "worfbench_recall_external_reference",
        "worfbench_f1_external_reference",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            score = row["action_prf1"]
            rule_score = row["rule_only_action_prf1"]
            chain = row["action_chain"]
            rule_chain = row["rule_only_action_chain"]
            branch = row.get("branch_coverage") or {}
            core_cls = row.get("core_business_classification") or {}
            worfbench = row.get("worfbench") or {}
            writer.writerow(
                {
                    "case_id": row["case_id"],
                    "version": row["version"],
                    "run_id": row["run_id"],
                    "gold_count": score["gold_count"],
                    "pred_count": score["pred_count"],
                    "rule_only_tp": rule_score["tp"],
                    "rule_only_action_precision": rule_score["precision"],
                    "rule_only_action_recall": rule_score["recall"],
                    "rule_only_action_f1": rule_score["f1"],
                    "rule_only_chain_precision": rule_chain["precision"],
                    "rule_only_chain_recall": rule_chain["recall"],
                    "rule_only_chain_f1": rule_chain["f1"],
                    "tp": score["tp"],
                    "action_precision": score["precision"],
                    "action_recall": score["recall"],
                    "action_f1": score["f1"],
                    "chain_precision": chain["precision"],
                    "chain_recall": chain["recall"],
                    "chain_f1": chain["f1"],
                    "judge_status": judge_status(row),
                    "unmatched_gold_count": len(row["unmatched_gold"]),
                    "unmatched_prediction_count": len(row["unmatched_prediction"]),
                    "gold_excluded_count": core_cls.get("gold_excluded_count"),
                    "pred_excluded_count": core_cls.get("pred_excluded_count"),
                    "branch_coverage": branch.get("branch_coverage"),
                    "branch_score": branch.get("branch_score"),
                    "branch_group_count": branch.get("group_count"),
                    "worfbench_status_external_reference": worfbench.get("status"),
                    "worfbench_precision_external_reference": worfbench.get("precision"),
                    "worfbench_recall_external_reference": worfbench.get("recall"),
                    "worfbench_f1_external_reference": worfbench.get("f1_score"),
                }
            )


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    aggregates: dict[str, dict[str, float | int]] = {}
    for version in sorted({row["version"] for row in rows}):
        version_rows = [row for row in rows if row["version"] == version]
        gold_total = sum(row["action_prf1"]["gold_count"] for row in version_rows)
        pred_total = sum(row["action_prf1"]["pred_count"] for row in version_rows)
        tp_total = sum(row["action_prf1"]["tp"] for row in version_rows)
        rule_tp_total = sum(row["rule_only_action_prf1"]["tp"] for row in version_rows)
        count = len(version_rows)
        worfbench_scores = [
            row["worfbench"] for row in version_rows if (row.get("worfbench") or {}).get("status") == "ok"
        ]
        aggregates[version] = {
            "case_count": count,
            "gold_total": gold_total,
            "prediction_total": pred_total,
            "rule_only_tp_total": rule_tp_total,
            "tp_total": tp_total,
            # Rule Match만(LLM 없이 재현 가능한 기준선) - Precision/Recall/F1 전부
            "macro_rule_only_action_precision": sum(
                row["rule_only_action_prf1"]["precision"] for row in version_rows
            ) / count,
            "macro_rule_only_action_recall": sum(
                row["rule_only_action_prf1"]["recall"] for row in version_rows
            ) / count,
            "macro_rule_only_action_f1": sum(
                row["rule_only_action_prf1"]["f1"] for row in version_rows
            ) / count,
            "macro_rule_only_chain_precision": sum(
                row["rule_only_action_chain"]["precision"] for row in version_rows
            ) / count,
            "macro_rule_only_chain_recall": sum(
                row["rule_only_action_chain"]["recall"] for row in version_rows
            ) / count,
            "macro_rule_only_chain_f1": sum(
                row["rule_only_action_chain"]["f1"] for row in version_rows
            ) / count,
            "micro_rule_only_action_f1": 2 * rule_tp_total / (gold_total + pred_total),
            # Rule Match + Judge Match(LLM 판정 포함, 실행 간 소폭 변동 가능)
            "macro_action_precision": sum(row["action_prf1"]["precision"] for row in version_rows) / count,
            "macro_action_recall": sum(row["action_prf1"]["recall"] for row in version_rows) / count,
            "macro_action_f1": sum(row["action_prf1"]["f1"] for row in version_rows) / count,
            "macro_chain_precision": sum(row["action_chain"]["precision"] for row in version_rows) / count,
            "macro_chain_recall": sum(row["action_chain"]["recall"] for row in version_rows) / count,
            "macro_chain_f1": sum(row["action_chain"]["f1"] for row in version_rows) / count,
            "micro_action_f1": 2 * tp_total / (gold_total + pred_total),
            # 외부 벤치마크 비교용(원본 WorFEval) - status가 "ok"인 케이스만 평균
            "worfbench_ok_case_count": len(worfbench_scores),
            "macro_worfbench_precision_external_reference": (
                sum(w["precision"] for w in worfbench_scores) / len(worfbench_scores) if worfbench_scores else None
            ),
            "macro_worfbench_recall_external_reference": (
                sum(w["recall"] for w in worfbench_scores) / len(worfbench_scores) if worfbench_scores else None
            ),
            "macro_worfbench_f1_external_reference": (
                sum(w["f1_score"] for w in worfbench_scores) / len(worfbench_scores) if worfbench_scores else None
            ),
        }
    return aggregates


def write_markdown(
    path: Path,
    rows: list[dict[str, Any]],
    coverage: dict[str, dict[str, list[str]]],
    aggregates: dict[str, dict[str, float | int]],
    scoring_errors: list[dict[str, str]] | None = None,
) -> None:
    lines = [
        "# Final Goldset 9 Evaluation Audit",
        "",
    ]
    if scoring_errors:
        lines.extend(
            [
                f"## ⚠ Scoring errors ({len(scoring_errors)}건 - 이 감사는 불완전합니다)",
                "",
            ]
        )
        for err in scoring_errors:
            lines.append(f"- {err['version']} {err['case_id']} ({err['run_id']}): {err['error']}")
        lines.append("")
    for version, status in coverage.items():
        missing = ", ".join(status["missing"]) or "none"
        lines.append(
            f"- {version}: {len(status['successful'])}/9 completed; missing: {missing}"
        )
        if status["runner_failed_artifact_scored"]:
            lines.append(
                f"- {version}: scored available artifacts after runner failure: "
                + ", ".join(status["runner_failed_artifact_scored"])
            )
    lines.extend(
        [
            "",
            "## Rule-only (LLM 호출 없음, 재현 가능한 기준선)",
            "",
            "| Agent | Cases | Gold | Pred | TP | Precision | Recall | F1 | Chain P | Chain R | Chain F1(LIS) | Micro F1 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for version, values in aggregates.items():
        lines.append(
            f"| {version} | {values['case_count']} | {values['gold_total']} | "
            f"{values['prediction_total']} | {values['rule_only_tp_total']} | "
            f"{values['macro_rule_only_action_precision']:.3f} | {values['macro_rule_only_action_recall']:.3f} | "
            f"{values['macro_rule_only_action_f1']:.3f} | "
            f"{values['macro_rule_only_chain_precision']:.3f} | {values['macro_rule_only_chain_recall']:.3f} | "
            f"{values['macro_rule_only_chain_f1']:.3f} | "
            f"{values['micro_rule_only_action_f1']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Judge-assisted (Rule Match + Judge Match, LLM 판정 포함 - 실행 간 소폭 변동 가능)",
            "",
            "| Agent | TP | Precision | Recall | F1 | Chain P | Chain R | Chain F1(LIS) | Micro F1 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for version, values in aggregates.items():
        lines.append(
            f"| {version} | {values['tp_total']} | {values['macro_action_precision']:.3f} | "
            f"{values['macro_action_recall']:.3f} | {values['macro_action_f1']:.3f} | "
            f"{values['macro_chain_precision']:.3f} | {values['macro_chain_recall']:.3f} | "
            f"{values['macro_chain_f1']:.3f} | {values['micro_action_f1']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## WorFBench (외부 벤치마크 비교용 - 원본 WorFEval t_eval_nodes, all-mpnet-base-v2 임베딩+임계값 0.6, 별도 경로/참고용)",
            "",
            "| Agent | ok 케이스 수 | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    def fmt3(value: float | None) -> str:
        return f"{value:.3f}" if isinstance(value, (int, float)) else "-"

    for version, values in aggregates.items():
        p = fmt3(values.get("macro_worfbench_precision_external_reference"))
        r = fmt3(values.get("macro_worfbench_recall_external_reference"))
        f1 = fmt3(values.get("macro_worfbench_f1_external_reference"))
        lines.append(f"| {version} | {values.get('worfbench_ok_case_count')} | {p} | {r} | {f1} |")
    lines.extend(
        [
            "",
            "| ID | Agent | Gold | Pred | Rule P | Rule R | Rule F1 | Rule Chain F1 | Judge P | Judge R | Judge F1 | Judge Chain F1 | WorFBench F1(참고) | Judge |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        score = row["action_prf1"]
        rule_score = row["rule_only_action_prf1"]
        worfbench = row.get("worfbench") or {}
        wf1 = worfbench.get("f1_score")
        lines.append(
            f"| {row['case_id']} | {row['version']} | {score['gold_count']} | {score['pred_count']} | "
            f"{rule_score['precision']:.3f} | {rule_score['recall']:.3f} | {rule_score['f1']:.3f} | "
            f"{row['rule_only_action_chain']['f1']:.3f} | "
            f"{score['precision']:.3f} | {score['recall']:.3f} | {score['f1']:.3f} | "
            f"{row['action_chain']['f1']:.3f} | "
            f"{f'{wf1:.3f}' if isinstance(wf1, (int, float)) else '-'} | "
            f"{judge_status(row)} |"
        )
    for row in rows:
        branch = row.get("branch_coverage") or {}
        core_cls = row.get("core_business_classification") or {}
        if branch.get("applicable"):
            branch_line = (
                f"- Branch Coverage/Score: `{branch['branch_coverage']:.3f}` / `{branch['branch_score']:.3f}` "
                f"(if 그룹 `{branch['group_count']}`개, 채점가능 분기 `{branch['scoreable_branch_count']}`개)"
            )
        else:
            branch_line = "- Branch Coverage/Score: 해당 없음 (핵심업무 액션 포함 if 분기 없음)"
        lines.extend(
            [
                "",
                f"## {row['case_id']} / {row['version']}",
                "",
                "- Gold only: " + (", ".join(item["label"] for item in row["unmatched_gold"]) or "none"),
                "- Prediction only: "
                + (", ".join(item["label"] for item in row["unmatched_prediction"]) or "none"),
                f"- Control structures: gold `{row['control_counts']['gold']}`, prediction `{row['control_counts']['prediction']}`",
                branch_line,
                f"- 핵심업무 분류 제외: gold `{core_cls.get('gold_excluded_count', 0)}`개, "
                f"prediction `{core_cls.get('pred_excluded_count', 0)}`개",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the cross-reviewed final 9 goldset against v2 and v3 runs.")
    parser.add_argument("--v2-manifest", type=Path, required=True)
    parser.add_argument("--v3-manifest", type=Path)
    parser.add_argument("--v2-0164-run-id")
    parser.add_argument(
        "--v2-run-id",
        action="append",
        default=[],
        metavar="CASE_ID=RUN_ID",
        help="Override a v2 case with a converted rerun; repeat as needed.",
    )
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    v2_overrides = parse_run_overrides(args.v2_run_id)
    if args.v2_0164_run_id:
        v2_overrides["0164"] = args.v2_0164_run_id
    v2_runs, v2_recovered = load_runs(
        args.v2_manifest.resolve(), v2_overrides, require_all=not args.allow_partial
    )
    version_runs = [("v2", v2_runs, v2_recovered)]
    if args.v3_manifest:
        v3_runs, v3_recovered = load_runs(
            args.v3_manifest.resolve(), require_all=not args.allow_partial
        )
        version_runs.append(
            ("v3", v3_runs, v3_recovered)
        )
    coverage = {
        version: {
            "successful": sorted(runs),
            "missing": sorted(FINAL_CASE_IDS - runs.keys()),
            "runner_failed_artifact_scored": recovered,
        }
        for version, runs, recovered in version_runs
    }
    # 케이스 하나가 실패해도(WorFBench는 이제 자체적으로 방어하지만, 그 외
    # 예상 못한 오류까지 포함해) 나머지 케이스는 계속 채점하고 audit.json이
    # 아예 안 만들어지는 걸 막는다 - run_eval_batch.py의 케이스 단위 격리
    # 원칙과 동일(Qodo 리뷰 지적, 2026-08-03).
    rows: list[dict[str, Any]] = []
    scoring_errors: list[dict[str, str]] = []
    for version, runs, _ in version_runs:
        for case_id in sorted(runs):
            try:
                rows.append(score_case(version, case_id, runs[case_id]))
            except Exception as exc:
                scoring_errors.append(
                    {"version": version, "case_id": case_id, "run_id": runs[case_id], "error": f"{type(exc).__name__}: {exc}"}
                )
                print(f"FAIL scoring {version} {case_id}: {exc}")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    aggregates = aggregate_rows(rows)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage,
        "scoring_errors": scoring_errors,
        "aggregates": aggregates,
        "results": rows,
    }
    (output_dir / "audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(output_dir / "summary.csv", rows)
    write_markdown(output_dir / "summary.md", rows, coverage, aggregates, scoring_errors)
    print(output_dir / "summary.md")

    # 케이스 단위 격리(위 for 루프)는 audit.json이 부분적으로라도 나오게 하려는
    # 것이지, 채점 실패를 성공으로 위장하려는 게 아니다 - Qodo 리뷰 지적
    # (2026-08-03): 예외를 삼키기만 하고 종료 코드/summary.md 어디에도 실패
    # 신호가 없으면 자동화 파이프라인이 불완전한 감사를 성공으로 오인할 수
    # 있다. --allow-partial 없이 실행했는데 채점 실패가 있으면 비정상 종료한다.
    if scoring_errors and not args.allow_partial:
        print(f"\n{len(scoring_errors)}건의 케이스 채점 실패 - --allow-partial 없이는 비정상 종료합니다:", file=sys.stderr)
        for err in scoring_errors:
            print(f"  {err['version']} {err['case_id']} ({err['run_id']}): {err['error']}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
