"""148개 규칙기반 후보 -> Exaone reasoning ON 판정(있음 49) -> 후순위 필터(DLL/RunMacro/
runApp/REST/미해결runTask, 정확한 package.action 매칭) 적용 -> 최종 34개 Main 후보.
GPT 전달용으로 goldset 원본 + Exaone 판정 로그 + 사유를 한 폴더에 정리한다.

3원칙 후순위 판정 기준:
1. 액션명만으로 일반적인 규칙기반 채점이 어려운가
2. 정답 여부를 판단하려면 스크립트/함수명/URL/Body/SQL 등 하위 구현까지 확인해야 하는가
3. 그 구현 방식이 업무상 반드시 필요한 선택이 아니라 다른 A360 액션으로 대체 가능한가
File/Folder/MessageBox/Screen/LogToFile/XML/JSONHandler/Dictionary/List는 이 필터에서 제외
(기능이 명확해서 별도 가치판단 대상 - core/salient 같은 옛 개념은 전부 폐기됨, 재사용 금지)."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from path_utils import full_470_dataset_dir, goldset_expansion_dir, workspace_root

sys.path.insert(0, str(workspace_root() / "A360-Assistant-Ops-rpa187" / "scripts" / "agent_flow_eval" / "processing"))
from resolve_subtask_coverage import resolve_transitive  # noqa: E402

DATASET = full_470_dataset_dir()
RESULTS_PATH = goldset_expansion_dir() / "exaone_judge_logs_148" / "_all_results.json"
BUNDLE_ROOT = goldset_expansion_dir() / "candidate_pool" / "gpt_bundle"

DEPRIORITIZE_PACKAGES_EXACT = {"DLL", "Python", "JavaScript", "VBScript"}
DEPRIORITIZE_ACTIONS_EXACT = {
    ("Excel_MS", "RunMacro"), ("Excel", "RunMacro"), ("Excel Advanced", "RunMacro"),
    ("Application", "runApp"),
    ("Rest", "restGet"), ("Rest", "restPost"), ("Rest", "restDelete"), ("Rest", "restPatch"),
}


def verdict(text: str) -> str:
    m = re.search(r"가치:\s*(있음|없음|애매함)", text)
    return m.group(1) if m else "UNKNOWN"


def deprioritize_reasons(pairs: set[tuple[str, str]], unresolved: set[str]) -> list[str]:
    reasons = []
    for pkg, act in pairs:
        if pkg in DEPRIORITIZE_PACKAGES_EXACT:
            reasons.append(f"{pkg}.{act}")
        if (pkg, act) in DEPRIORITIZE_ACTIONS_EXACT:
            reasons.append(f"{pkg}.{act}")
        if "soap" in pkg.lower():
            reasons.append(f"{pkg}.{act}(SOAP)")
        if pkg.lower() == "database" and any(k in act.lower() for k in ("stored", "procedure", "sql", "execute")):
            reasons.append(f"{pkg}.{act}(SQL/StoredProc)")
    if ("TaskBot", "runTask") in pairs and unresolved:
        reasons.append("TaskBot.runTask(하위Bot 해석실패)")
    return sorted(set(reasons))


def main() -> None:
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    on_yes = [r for r in results if verdict(r["reasoning_on"]["content"]) == "있음"]

    main_dir = BUNDLE_ROOT / "candidates_main_34"
    deprior_dir = BUNDLE_ROOT / "candidates_deprioritized_15"
    logs_dir = BUNDLE_ROOT / "exaone_judge_logs"
    for d in (main_dir, deprior_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    main_rows = []
    deprior_rows = []

    for r in on_yes:
        bot_name = r["bot_name"]
        source_file = r["source_file"]
        stem = source_file[: -len(".goldset.json")]
        bot_dir = DATASET / bot_name
        pairs, unresolved = resolve_transitive(bot_dir / "workflows", stem, visited=set())
        reasons = deprioritize_reasons(set(pairs), unresolved)

        gold_path = bot_dir / "workflows" / source_file
        safe_name = f"{bot_name}__{stem}".replace("/", "_")

        (logs_dir / f"{safe_name}.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if reasons:
            shutil.copy(gold_path, deprior_dir / f"{safe_name}.goldset.json")
            deprior_rows.append({"bot": bot_name, "file": source_file, "action_count": r["action_count"], "reasons": reasons})
        else:
            shutil.copy(gold_path, main_dir / f"{safe_name}.goldset.json")
            main_rows.append({"bot": bot_name, "file": source_file, "action_count": r["action_count"]})

    summary_lines = [
        "# 골드셋 확장 후보 - GPT 검토용 번들",
        "",
        "## 선정 과정",
        "",
        "```",
        "470개 전체(817 워크플로우)",
        "  -> RAG 카탈로그 정규화 완전일치 + main workflow + 액션수>=3   148개",
        "  -> Exaone(EXAONE-4.0-32B, reasoning ON) '골드셋 가치 있음' 판정   49개",
        "  -> 후순위 필터(정확한 package.action 매칭, DLL/Python/JS/VBScript/",
        "     RunMacro/runApp/REST/미해결TaskBot.runTask만 제외)          34개 (Main 후보)",
        "                                                                15개 (후순위 보존)",
        "```",
        "",
        f"## Main 후보 ({len(main_rows)}개)",
        "",
        "| 봇 | 파일 | 액션수 |",
        "|---|---|---|",
    ]
    for row in sorted(main_rows, key=lambda x: x["bot"]):
        summary_lines.append(f"| {row['bot']} | {row['file']} | {row['action_count']} |")

    summary_lines += ["", f"## 후순위 보존 ({len(deprior_rows)}개) - 사유 포함", "", "| 봇 | 파일 | 액션수 | 후순위 사유 |", "|---|---|---|---|"]
    for row in sorted(deprior_rows, key=lambda x: x["bot"]):
        summary_lines.append(f"| {row['bot']} | {row['file']} | {row['action_count']} | {', '.join(row['reasons'])} |")

    (BUNDLE_ROOT / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"Main 후보: {len(main_rows)}개 -> {main_dir}")
    print(f"후순위 보존: {len(deprior_rows)}개 -> {deprior_dir}")
    print(f"Exaone 판정 로그: {len(on_yes)}개 -> {logs_dir}")
    print(f"요약: {BUNDLE_ROOT / 'summary.md'}")


if __name__ == "__main__":
    main()
