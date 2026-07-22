"""로컬모델 생성 답변(cs1200_judge_gpt4omini, --generator local 시절 기본값) vs
gpt-4o-mini 생성 답변(cs1200_gen_gpt4omini, --generator gpt4o-mini)을 케이스별로
나란히 비교하는 마크다운 파일을 만든다. eval_runs.jsonl에서 최신 레코드만 쓴다
(cs1200_gen_gpt4omini는 실수로 두 번 돌아가서 20건이 쌓였음 — evaluation_id가 더
큰 쪽만 채택해서 중복 제거).
"""
import json
from pathlib import Path

EVAL_RUNS = Path("data/eval_runs.jsonl")
OUT_PATH = Path(
    r"C:/Users/KDH/AppData/Local/Temp/claude/c--Users-KDH-Documents-VisualStudio-Code-A360-Assistant/"
    r"2dfe4831-0385-4ec6-b182-3318d6c846ce/scratchpad/generation_quality_comparison.md"
)

LOCAL_LABEL = "cs1200_judge_gpt4omini"  # 이 라벨은 --generator 옵션 생기기 전 실행이라 생성은 항상 로컬모델이었음
GPT_LABEL = "cs1200_gen_gpt4omini"


def load_latest_by_case(label: str) -> dict:
    by_eval_id: dict[str, dict] = {}
    with EVAL_RUNS.open(encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("agent_label") != label:
                continue
            by_eval_id.setdefault(rec["evaluation_id"], []).append(rec)
    latest_eval_id = max(by_eval_id.keys(), key=lambda e: int(e.rsplit("_", 1)[1]))
    return {r["case_id"]: r for r in by_eval_id[latest_eval_id]}, latest_eval_id


def metric_map(rec: dict) -> dict:
    return {m["name"]: m["value"] for m in rec.get("metrics", [])}


def main() -> None:
    local_recs, local_eval_id = load_latest_by_case(LOCAL_LABEL)
    gpt_recs, gpt_eval_id = load_latest_by_case(GPT_LABEL)

    common_case_ids = [cid for cid in local_recs if cid in gpt_recs]

    lines = []
    lines.append("# 로컬모델 vs gpt-4o-mini 생성 답변 품질 비교")
    lines.append("")
    lines.append(f"- 검색 테이블: `rag_documents_eval_cs1200_ov0` (top_k=5, vector-only, reranker 없음)")
    lines.append(f"- 채점자(judge): gpt-4o-mini (양쪽 동일 — 생성자만 다름)")
    lines.append(f"- 로컬 생성 evaluation_id: `{local_eval_id}` (agent_label={LOCAL_LABEL})")
    lines.append(f"- gpt-4o-mini 생성 evaluation_id: `{gpt_eval_id}` (agent_label={GPT_LABEL})")
    lines.append(f"- 비교 케이스 수: {len(common_case_ids)}건")
    lines.append("")
    lines.append(
        "| case_id | faithfulness (local/gpt) | answer_correctness (local/gpt) | answer_relevancy (local/gpt) |"
    )
    lines.append("|---|---|---|---|")
    for cid in common_case_ids:
        lm = metric_map(local_recs[cid])
        gm = metric_map(gpt_recs[cid])
        lines.append(
            f"| {cid} "
            f"| {lm.get('faithfulness', 0):.2f} / {gm.get('faithfulness', 0):.2f} "
            f"| {lm.get('answer_correctness', 0):.2f} / {gm.get('answer_correctness', 0):.2f} "
            f"| {lm.get('answer_relevancy', 0):.2f} / {gm.get('answer_relevancy', 0):.2f} |"
        )
    lines.append("")

    for i, cid in enumerate(common_case_ids, start=1):
        local_raw = local_recs[cid]["raw"]
        gpt_raw = gpt_recs[cid]["raw"]
        lm = metric_map(local_recs[cid])
        gm = metric_map(gpt_recs[cid])
        lines.append(f"## {i}. {cid}")
        lines.append("")
        lines.append(f"**질문**: {local_raw['question']}")
        lines.append("")
        lines.append(f"**정답(ground truth)**: {local_raw['ground_truth']}")
        lines.append("")
        lines.append(
            f"**로컬모델 답변** (faithfulness={lm.get('faithfulness', 0):.2f}, "
            f"answer_correctness={lm.get('answer_correctness', 0):.2f}, "
            f"answer_relevancy={lm.get('answer_relevancy', 0):.2f})"
        )
        lines.append("")
        lines.append(f"> {local_raw['answer']}")
        lines.append("")
        lines.append(
            f"**gpt-4o-mini 답변** (faithfulness={gm.get('faithfulness', 0):.2f}, "
            f"answer_correctness={gm.get('answer_correctness', 0):.2f}, "
            f"answer_relevancy={gm.get('answer_relevancy', 0):.2f})"
        )
        lines.append("")
        lines.append(f"> {gpt_raw['answer']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"WROTE: {OUT_PATH} ({len(common_case_ids)}건, {OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
