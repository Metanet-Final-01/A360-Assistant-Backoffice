"""148개 중 Exaone(reasoning ON)이 '있음'으로 판정하지 않은 나머지(없음/애매함)를
gpt_bundle/gpt_final_classification 밑에 별도 폴더로 모은다.
(GPT에게 처음 34개 번들 만들 때는 아직 안 보여준 나머지 풀 - 나중에 다양성 보강용으로 씀)"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
import shutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from path_utils import full_470_dataset_dir, goldset_expansion_dir  # noqa: E402

DATASET = full_470_dataset_dir()
RESULTS_PATH = goldset_expansion_dir() / "exaone_judge_logs_148" / "_all_results.json"
OUT_DIR = goldset_expansion_dir() / "candidate_pool" / "gpt_bundle" / "candidates_exaone_rejected_99"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def verdict(text: str) -> str:
    m = re.search(r"가치:\s*(있음|없음|애매함)", text)
    return m.group(1) if m else "UNKNOWN"


def reason(text: str) -> str:
    m = re.search(r"이유:\s*(.+)", text, re.DOTALL)
    return m.group(1).strip().replace("\n", " ") if m else ""


def main() -> None:
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    rest = [r for r in results if verdict(r["reasoning_on"]["content"]) != "있음"]

    rows = []
    for r in rest:
        bot_name = r["bot_name"]
        source_file = r["source_file"]
        v = verdict(r["reasoning_on"]["content"])
        why = reason(r["reasoning_on"]["content"])

        gold_path = DATASET / bot_name / "workflows" / source_file
        safe_name = f"{bot_name}__{source_file}".replace("/", "_")

        if gold_path.exists():
            shutil.copy(gold_path, OUT_DIR / safe_name)
        else:
            print(f"WARNING: 원본 없음 - {gold_path}")

        rows.append({"bot": bot_name, "file": source_file, "verdict": v, "reason": why, "action_count": r["action_count"]})

    summary_lines = [
        "# Exaone 판정 나머지 (148개 중 '있음' 49개를 제외한 99개)",
        "",
        "reasoning ON 기준 '없음' 또는 '애매함'으로 판정된 항목. GPT 34개 번들에는 포함되지 않았던 풀.",
        "",
        "| 봇 | 파일 | 판정 | 액션수 | 이유 |",
        "|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda x: (x["verdict"], x["bot"])):
        summary_lines.append(f"| {row['bot']} | {row['file']} | {row['verdict']} | {row['action_count']} | {row['reason']} |")

    (OUT_DIR / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    n_no = sum(1 for r in rows if r["verdict"] == "없음")
    n_amb = sum(1 for r in rows if r["verdict"] == "애매함")
    n_unk = sum(1 for r in rows if r["verdict"] == "UNKNOWN")
    print(f"총 {len(rows)}개 (없음 {n_no} / 애매함 {n_amb} / 판정불명 {n_unk})")
    print(f"저장 위치: {OUT_DIR}")


if __name__ == "__main__":
    main()
