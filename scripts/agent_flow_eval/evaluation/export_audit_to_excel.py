"""audit_final_goldset.py가 만든 audit.json을 사람이 검수하기 쉬운 다중 시트
엑셀로 정리한다. 요약 숫자만이 아니라 매칭 쌍/미매칭/Judge 판정 로그/핵심업무
분류 로그/분기 진단까지 세부사항 전부를 시트로 나눠 담는다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.utils import get_column_letter


def load_audit(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_overview_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overview = []
    for row in rows:
        score = row["action_prf1"]
        rule_score = row["rule_only_action_prf1"]
        branch = row.get("branch_coverage") or {}
        core_cls = row.get("core_business_classification") or {}
        overview.append(
            {
                "case_id": row["case_id"],
                "version": row["version"],
                "run_id": row["run_id"],
                "gold_count_core": score["gold_count"],
                "pred_count_core": score["pred_count"],
                "gold_excluded_non_core": core_cls.get("gold_excluded_count"),
                "pred_excluded_non_core": core_cls.get("pred_excluded_count"),
                "rule_only_tp": rule_score["tp"],
                "rule_only_precision": rule_score["precision"],
                "rule_only_recall": rule_score["recall"],
                "rule_only_f1": rule_score["f1"],
                "rule_only_chain_f1": row["rule_only_action_chain"]["f1"],
                "judge_assisted_tp": score["tp"],
                "judge_assisted_precision": score["precision"],
                "judge_assisted_recall": score["recall"],
                "judge_assisted_f1": score["f1"],
                "judge_assisted_chain_f1": row["action_chain"]["f1"],
                "branch_applicable": branch.get("applicable"),
                "branch_coverage": branch.get("branch_coverage"),
                "branch_score": branch.get("branch_score"),
                "branch_group_count": branch.get("group_count"),
                "branch_scoreable_count": branch.get("scoreable_branch_count"),
                "control_if_gold": row["control_counts"]["gold"].get("if"),
                "control_loop_gold": row["control_counts"]["gold"].get("loop"),
                "control_try_gold": row["control_counts"]["gold"].get("try"),
                "unmatched_gold_count": len(row["unmatched_gold"]),
                "unmatched_prediction_count": len(row["unmatched_prediction"]),
            }
        )
    return overview


def build_matches_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        for m in row["matches"]:
            out.append(
                {
                    "case_id": row["case_id"],
                    "version": row["version"],
                    "match_type": m["match_type"],
                    "canonical_label": m["canonical_label"],
                    "gold_label": m["gold_label"],
                    "prediction_label": m["prediction_label"],
                    "gold_id": m["gold_id"],
                    "pred_id": m["pred_id"],
                }
            )
    return out


def build_unmatched_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        for item in row["unmatched_gold"]:
            out.append(
                {
                    "case_id": row["case_id"],
                    "version": row["version"],
                    "side": "gold_only(FN)",
                    "label": item["label"],
                    "canonical_label": item["canonical_label"],
                    "uid": item["uid"],
                }
            )
        for item in row["unmatched_prediction"]:
            out.append(
                {
                    "case_id": row["case_id"],
                    "version": row["version"],
                    "side": "prediction_only(FP)",
                    "label": item["label"],
                    "canonical_label": item["canonical_label"],
                    "uid": item["uid"],
                }
            )
    return out


def build_judge_log_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        gold_by_uid = row.get("gold_actions_by_uid") or {}
        pred_by_uid = row.get("pred_actions_by_uid") or {}
        for entry in row["judge_log"]:
            out.append(
                {
                    "case_id": row["case_id"],
                    "version": row["version"],
                    "gold_id": entry.get("gold_id"),
                    "gold_label": (gold_by_uid.get(entry.get("gold_id"), {}) or {}).get("canonical_label"),
                    "pred_id": entry.get("pred_id"),
                    "prediction_label": (pred_by_uid.get(entry.get("pred_id"), {}) or {}).get("canonical_label"),
                    "similarity": entry.get("similarity"),
                    "verdict": entry.get("verdict", entry.get("status")),
                    "reason": entry.get("reason") or entry.get("error"),
                    "stage": entry.get("stage"),
                }
            )
    return out


def build_core_business_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        core_cls = row.get("core_business_classification") or {}
        for side, log_key in (("gold", "gold_log"), ("pred", "pred_log")):
            for entry in core_cls.get(log_key) or []:
                out.append(
                    {
                        "case_id": row["case_id"],
                        "version": row["version"],
                        "side": side,
                        "uid": entry.get("uid"),
                        "label": entry.get("label"),
                        "params": entry.get("params"),
                        "verdict": entry.get("verdict"),
                        "source": entry.get("source"),
                        "reason": entry.get("reason"),
                    }
                )
    return out


def build_branch_detail_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        branch = row.get("branch_coverage") or {}
        for group in branch.get("groups") or []:
            for b in group["branches"]:
                out.append(
                    {
                        "case_id": row["case_id"],
                        "version": row["version"],
                        "group_id": group["group_id"],
                        "branch_name": b["branch_name"],
                        "core_action_count": b["core_action_count"],
                        "matched_count": b["matched_count"],
                        "coverage_ratio": b["coverage_ratio"],
                    }
                )
    return out


def build_aggregates_rows(aggregates: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"version": version, **values} for version, values in aggregates.items()]


def autosize_columns(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    worksheet = writer.sheets[sheet_name]
    for idx, column in enumerate(df.columns):
        cell_lengths = [len(str(value)) for value in df[column].tolist()]
        max_len = max([len(str(column)), *cell_lengths]) + 2
        worksheet.column_dimensions[get_column_letter(idx + 1)].width = min(max_len, 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="audit_final_goldset.py의 audit.json을 상세 엑셀로 변환")
    parser.add_argument("audit_json", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    payload = load_audit(args.audit_json)
    rows = payload["results"]
    output_path = args.output or args.audit_json.with_suffix("").with_name(args.audit_json.stem + "_detail.xlsx")

    sheets: dict[str, pd.DataFrame] = {
        "Overview": pd.DataFrame(build_overview_rows(rows)),
        "Aggregates": pd.DataFrame(build_aggregates_rows(payload["aggregates"])),
        "Matches": pd.DataFrame(build_matches_rows(rows)),
        "Unmatched": pd.DataFrame(build_unmatched_rows(rows)),
        "JudgeLog": pd.DataFrame(build_judge_log_rows(rows)),
        "CoreBusinessClassification": pd.DataFrame(build_core_business_rows(rows)),
        "BranchCoverageDetail": pd.DataFrame(build_branch_detail_rows(rows)),
    }

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            if not df.empty:
                autosize_columns(writer, name, df)

    print(f"wrote {output_path} ({sum(len(df) for df in sheets.values())} total rows across {len(sheets)} sheets)")


if __name__ == "__main__":
    main()
