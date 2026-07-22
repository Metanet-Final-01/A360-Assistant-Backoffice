"""1단계: chunk_size 5개 후보(300/600/900/1200/1500, overlap=0) 검색 품질 비교.

로컬 chunk_size 후보 테이블에 벡터 유사도 검색만 적용해(BM25/reranker 없음 —
chunk_size 자체의 효과만 순수하게 보기 위함) 답변을 생성하고 RAGAS 4개 지표
(faithfulness/answer_relevancy/context_precision/context_recall) + 검색 직접
지표(Hit@1/3/5, MRR, evidence coverage)로 채점한다. 모든 후보에 동일한 embedding
모델·distance metric(pgvector 코사인)·splitter 규칙·생성 프롬프트·생성 모델을 쓴다
— chunk_size 자체 말고 다른 변수가 안 섞이게 하기 위함.

질문 임베딩은 chunk_size와 무관하므로 케이스당 한 번만 만들어 5개 후보가 공유한다
(비용 1/5로 절감). 결과는 전체/문서유형별(doc_page·action_schema)로 나눠 보여준다
— 두 유형은 성격이 달라(구조화된 파라미터 목록 vs 서술형 절차) 최적 chunk_size가
다를 수 있고, 표본이 각각 69건/30건이라 따로 볼 만하다(package_overview는 1건뿐이라
전체 집계에만 포함하고 별도로는 안 나눈다).

RAGAS 4개 지표를 하나로 평균해서 승자를 정하지 않는다 — 지표별·문항별 원본을
결과 JSON에 전부 남기고(all_rows), 요약 출력은 참고용일 뿐이다.

실행: ops-server/.venv의 파이썬으로 실행해야 한다(ragas·langchain_openai가 거기 있음).
프로덕션 검색 경로는 전혀 건드리지 않는다 — 이 스크립트가 읽는 건 평가 전용 로컬
후보 테이블(rag_documents_eval_cs*)뿐이다.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
# 파일로 리다이렉트하면 파이썬이 완전 버퍼링으로 바뀌어서 print()가 한참 안 보인다
# (백그라운드 실행 중 "로그가 0바이트"로 오해했던 실제 문제) — 줄 단위로 강제 flush.
sys.stdout.reconfigure(line_buffering=True)

_OPS_ROOT = Path(__file__).resolve().parents[3] / "ops-server"
_GOLDSET_PATH = _OPS_ROOT / "backend" / "app" / "eval" / "ragas_eval" / "cases" / "rag_goldset_v1.json"
_RESULTS_DIR = Path(__file__).resolve().parent / "results"

CHUNK_SIZES = [300, 600, 900, 1200, 1500]
OVERLAP = 0  # 1단계는 overlap 고정 — overlap 자체는 2단계에서 chunk_size 승자 기준으로 비교
K = 5  # 검색 top-k — 라이브 러너(ops-server runner.py)의 기본 limit과 동일하게 맞춤
JUDGE_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-small"
PROMPT_VERSION = "answer_v1"  # _ANSWER_SYSTEM_PROMPT가 바뀌면 올린다 — 과거 실행과 비교 시 참고용

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / "rag-server" / ".env")
from openai import OpenAI

import ragas
from ragas import SingleTurnSample
from ragas.cost import get_token_usage_for_openai
from ragas.dataset_schema import EvaluationDataset
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.evaluation import evaluate as ragas_evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    AnswerCorrectness,
    AnswerRelevancy,
    Faithfulness,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
)
from ragas.run_config import RunConfig
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# 기본 max_workers=16이 동시에 몰리면서 180초 타임아웃을 대량 유발했다. 4→2로 낮춰봐도
# 100케이스 실측(웹 UI 경로)에서 매번 OpenAI rate limit(gpt-4o-mini 실측: 200K TPM/500 RPM)에
# 걸려 재시도가 누적되며 항목당 처리시간이 1초→11~14초로 급감하는 현상이 반복됐다 —
# "안전한 동시성 값"을 계속 찾기보다 완전 순차(max_workers=1)로 확정. 느리지만 버스트로
# 한도를 넘겨 재시도 폭주에 빠질 위험 자체가 없다.
_RAGAS_RUN_CONFIG = RunConfig(timeout=180, max_workers=1, max_retries=10)

# gpt-4o-mini 공개 단가(2026-07 기준, OpenAI 공식) — 프로젝트 .env의 LLM_INPUT/OUTPUT_COST_PER_1M은
# 실제 프로덕션 모델(gpt-5.4-mini) 단가라 여기선 안 쓴다.
_GPT4O_MINI_INPUT_PER_1M = 0.15
_GPT4O_MINI_OUTPUT_PER_1M = 0.60
_EMBEDDING_INPUT_PER_1M = 0.02


def local_dsn() -> str:
    host = os.getenv("DATABASE_HOST") or "127.0.0.1"
    port = os.getenv("DATABASE_PORT") or "5432"
    name = os.getenv("DATABASE_NAME") or "a360"
    user = os.getenv("DATABASE_USERNAME") or "a360_admin"
    password = os.getenv("DATABASE_PASSWORD") or "a360_local_password"
    return f"host={host} port={port} dbname={name} user={user} password={password}"


_ANSWER_SYSTEM_PROMPT = (
    "당신은 A360(RPA) 패키지/액션 문서를 근거로 질문에 답하는 어시스턴트입니다. "
    "아래 [검색된 문서]에 있는 내용만 근거로 답하세요. 문서에 없는 내용은 지어내지 말고 "
    "'문서에서 찾을 수 없습니다'라고 답하세요. 간결하게 답하세요."
)


def _load_approved_cases() -> list[dict]:
    raw = json.loads(_GOLDSET_PATH.read_text(encoding="utf-8"))
    return [c for c in raw if c.get("status") == "approved"]


def _tag_source_types(conn, cases: list[dict]) -> dict[str, str]:
    """case_id → source_type('doc_page'|'action_schema'|'package_overview'|'unknown'|'mixed')."""
    doc_ids: set[str] = set()
    for c in cases:
        doc_ids.update(c.get("reference_doc_ids", []))
        doc_ids.update(rc["source_document_id"] for rc in c.get("reference_contexts", []))
    with conn.cursor() as cur:
        cur.execute("SELECT id, source_type FROM source_documents WHERE id = ANY(%s)", (list(doc_ids),))
        id_to_type = dict(cur.fetchall())

    result: dict[str, str] = {}
    for c in cases:
        types_in_case: set[str] = set()
        for d in c.get("reference_doc_ids", []):
            if d in id_to_type:
                types_in_case.add(id_to_type[d])
        for rc in c.get("reference_contexts", []):
            d = rc["source_document_id"]
            if d in id_to_type:
                types_in_case.add(id_to_type[d])
        if not types_in_case:
            result[c["case_id"]] = "unknown"
        elif len(types_in_case) > 1:
            result[c["case_id"]] = "mixed"
        else:
            result[c["case_id"]] = next(iter(types_in_case))
    return result


def _relevant_doc_ids(case: dict) -> set[str]:
    """이 케이스의 정답 근거로 지목된 원본 문서 id 집합 — Hit@k/MRR의 "정답" 기준.
    reference_doc_ids와 reference_contexts의 source_document_id 둘 다 이 문서 id
    체계를 쓴다(source_documents.id == 후보 테이블의 parent_id, 실측 확인됨)."""
    ids = set(case.get("reference_doc_ids", []))
    ids.update(rc["source_document_id"] for rc in case.get("reference_contexts", []))
    return ids


def _hit_and_rr(retrieved_ids: list[str], relevant_ids: set[str]) -> dict:
    """검색 직접 지표 — Hit@1/3/5(정답 문서가 그 순위 안에 있으면 1)와 reciprocal rank."""
    if not relevant_ids:
        return {"hit_at_1": None, "hit_at_3": None, "hit_at_5": None, "reciprocal_rank": None}
    rank = next((i + 1 for i, rid in enumerate(retrieved_ids) if rid in relevant_ids), None)
    return {
        "hit_at_1": int(rank is not None and rank <= 1),
        "hit_at_3": int(rank is not None and rank <= 3),
        "hit_at_5": int(rank is not None and rank <= 5),
        "reciprocal_rank": (1.0 / rank) if rank else 0.0,
    }


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _evidence_coverage(case: dict, retrieved_contents: list[str]) -> float | None:
    """reference_contexts의 근거 스니펫 중 몇 %가 이번에 검색된 청크들의 텍스트 안에
    (공백 무시하고) 실제로 들어있는지 — RAGAS의 LLM 판정과 별개로, 검색이 골드셋
    작성 시 사람이 지목한 그 원문 구간을 실제로 가져왔는지 직접 확인하는 지표.
    reference_contexts가 없는 케이스는 계산 대상이 아니라 None."""
    snippets = [rc["snippet"] for rc in case.get("reference_contexts", [])]
    if not snippets:
        return None
    haystack = _normalize_ws("\n".join(retrieved_contents))
    found = sum(1 for s in snippets if _normalize_ws(s) in haystack)
    return found / len(snippets)


def _write_results(path: Path, meta: dict, rows: list[dict]) -> None:
    """매 chunk_size마다 통째로 다시 쓴다(덮어쓰기) — 중간에 죽어도 그 시점까지 끝난
    chunk_size들은 파일에 남는다. 케이스 100개×5 정도 규모라 매번 새로 써도 가볍다."""
    path.write_text(json.dumps({**meta, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    cases = _load_approved_cases()
    print(f"승인 케이스 {len(cases)}건 로드")

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _RESULTS_DIR / f"chunk_size_comparison_{int(time.time())}.json"

    ragas_llm = LangchainLLMWrapper(ChatOpenAI(model=JUDGE_MODEL, api_key=os.environ["OPENAI_API_KEY"], temperature=0))
    ragas_embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(api_key=os.environ["OPENAI_API_KEY"], model=EMBEDDING_MODEL)
    )
    metrics = [
        Faithfulness(llm=ragas_llm),
        AnswerRelevancy(llm=ragas_llm, embeddings=ragas_embeddings),
        LLMContextPrecisionWithReference(llm=ragas_llm),
        LLMContextRecall(llm=ragas_llm),
        # 검색 근거 대비 지표(위 4개)만으로는 "정답 자체와 얼마나 일치하는가"가 안 잡힌다 —
        # AnswerCorrectness는 ground_truth와 직접 비교(사실 일치 + 의미 유사도)하는 표준 지표.
        AnswerCorrectness(llm=ragas_llm, embeddings=ragas_embeddings),
    ]
    cases_by_id = {c["case_id"]: c for c in cases}

    all_rows: list[dict] = []
    embed_tokens = 0
    gen_input_tokens = 0
    gen_output_tokens = 0
    total_ragas_cost = 0.0

    with psycopg.connect(local_dsn()) as conn:
        case_source_type = _tag_source_types(conn, cases)
        print("케이스별 문서유형:", {t: sum(1 for v in case_source_type.values() if v == t) for t in set(case_source_type.values())})

        # 질문 임베딩 — chunk_size 5개 후보가 공유(비용 1/5), 한 번만 만들고 재사용.
        question_vectors: dict[str, list[float]] = {}
        for c in cases:
            resp = client.embeddings.create(model=EMBEDDING_MODEL, input=c["question"])
            embed_tokens += resp.usage.total_tokens
            question_vectors[c["case_id"]] = resp.data[0].embedding
        print(f"질문 임베딩 완료 ({embed_tokens} 토큰)")

        for size in CHUNK_SIZES:
            table = f"rag_documents_eval_cs{size}_ov{OVERLAP}"
            samples: list[SingleTurnSample] = []
            case_ids: list[str] = []
            retrieval_meta: dict[str, dict] = {}
            t0 = time.time()
            with conn.cursor() as cur:
                for c in cases:
                    vec = question_vectors[c["case_id"]]
                    cur.execute(
                        f"SELECT parent_id, content FROM {table} ORDER BY embedding <=> %s::vector LIMIT %s",
                        (vec, K),
                    )
                    rows = cur.fetchall()
                    retrieved_ids = [r[0] for r in rows]
                    contexts = [r[1] for r in rows]
                    context_block = "\n\n".join(f"[문서 {i + 1}]\n{ctx}" for i, ctx in enumerate(contexts))
                    resp = client.chat.completions.create(
                        model=JUDGE_MODEL,
                        messages=[
                            {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
                            {"role": "user", "content": f"[검색된 문서]\n{context_block}\n\n[질문]\n{c['question']}"},
                        ],
                        temperature=0,
                    )
                    gen_input_tokens += resp.usage.prompt_tokens
                    gen_output_tokens += resp.usage.completion_tokens
                    answer = resp.choices[0].message.content or ""
                    samples.append(SingleTurnSample(
                        user_input=c["question"], retrieved_contexts=contexts,
                        response=answer, reference=c["ground_truth"],
                    ))
                    case_ids.append(c["case_id"])
                    retrieval_meta[c["case_id"]] = {
                        "retrieved_parent_ids": retrieved_ids,
                        "retrieved_total_chars": sum(len(x) for x in contexts),
                        **_hit_and_rr(retrieved_ids, _relevant_doc_ids(c)),
                        "evidence_coverage": _evidence_coverage(c, contexts),
                    }
            retrieval_elapsed = time.time() - t0
            print(f"chunk_size={size}: 검색+생성 완료 ({len(samples)}건, {retrieval_elapsed:.0f}초)")

            t_ragas = time.time()
            result = ragas_evaluate(
                dataset=EvaluationDataset(samples=samples), metrics=metrics,
                token_usage_parser=get_token_usage_for_openai, run_config=_RAGAS_RUN_CONFIG,
            )
            ragas_elapsed = time.time() - t_ragas
            cost = result.total_cost(
                cost_per_input_token=_GPT4O_MINI_INPUT_PER_1M / 1_000_000,
                cost_per_output_token=_GPT4O_MINI_OUTPUT_PER_1M / 1_000_000,
            )
            total_ragas_cost += cost
            df = result.to_pandas()
            n_failed = 0
            for i, case_id in enumerate(case_ids):
                row = df.iloc[i]
                case = cases_by_id[case_id]
                scores = {
                    "faithfulness": _safe_float(row.get("faithfulness")),
                    "answer_relevancy": _safe_float(row.get("answer_relevancy")),
                    "context_precision": _safe_float(row.get("llm_context_precision_with_reference")),
                    "context_recall": _safe_float(row.get("context_recall")),
                    "answer_correctness": _safe_float(row.get("answer_correctness")),
                }
                if any(v is None for v in scores.values()):
                    n_failed += 1
                all_rows.append({
                    "chunk_size": size,
                    "chunk_overlap": OVERLAP,
                    "overlap_ratio": round(OVERLAP / size, 4),
                    "top_k": K,
                    "case_id": case_id,
                    "source_type": case_source_type.get(case_id, "unknown"),
                    "question_type": case.get("question_type"),
                    "embedding_model": EMBEDDING_MODEL,
                    "generator_model": JUDGE_MODEL,
                    "evaluator_model": JUDGE_MODEL,
                    "prompt_version": PROMPT_VERSION,
                    "ragas_version": ragas.__version__,
                    **scores,
                    **retrieval_meta[case_id],
                    "retrieval_elapsed_sec": round(retrieval_elapsed / len(case_ids), 3),
                    "ragas_elapsed_sec_batch": round(ragas_elapsed, 1),
                })

            embed_cost = embed_tokens * _EMBEDDING_INPUT_PER_1M / 1_000_000
            gen_cost = (
                gen_input_tokens * _GPT4O_MINI_INPUT_PER_1M / 1_000_000
                + gen_output_tokens * _GPT4O_MINI_OUTPUT_PER_1M / 1_000_000
            )
            total_cost_so_far = embed_cost + gen_cost + total_ragas_cost
            print(
                f"chunk_size={size}: RAGAS 채점 완료 (지표 일부 실패 {n_failed}/{len(case_ids)}건, "
                f"누적 비용 ${total_cost_so_far:.4f}, 이 단계 {ragas_elapsed:.0f}초)"
            )

            # chunk_size 하나 끝날 때마다 즉시 저장 — 중간에 죽어도 여기까지는 안 날아간다.
            _write_results(
                out_path,
                {
                    "total_cost_usd": total_cost_so_far,
                    "cost_breakdown": {"embedding": embed_cost, "generation": gen_cost, "ragas_judge": total_ragas_cost},
                    "completed_chunk_sizes": [s for s in CHUNK_SIZES if s <= size],
                    "judge_model": JUDGE_MODEL, "embedding_model": EMBEDDING_MODEL,
                    "ragas_version": ragas.__version__, "prompt_version": PROMPT_VERSION, "top_k": K,
                },
                all_rows,
            )
            print(f"  → 중간 저장: {out_path}")

    embed_cost = embed_tokens * _EMBEDDING_INPUT_PER_1M / 1_000_000
    gen_cost = (
        gen_input_tokens * _GPT4O_MINI_INPUT_PER_1M / 1_000_000
        + gen_output_tokens * _GPT4O_MINI_OUTPUT_PER_1M / 1_000_000
    )
    total_cost = embed_cost + gen_cost + total_ragas_cost
    print(f"\n최종 결과: {out_path} (chunk_size별로 이미 다 저장돼 있음)")
    print(f"총 비용: ${total_cost:.2f} (임베딩 ${embed_cost:.4f} + 생성 ${gen_cost:.4f} + RAGAS ${total_ragas_cost:.4f})")

    _print_summary(all_rows)


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        f = float(value)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _avg(group: list[dict], metric: str) -> str:
    values = [r[metric] for r in group if r.get(metric) is not None]
    return f"{sum(values) / len(values):.3f}" if values else "N/A"


def _print_summary(rows: list[dict]) -> None:
    for label, filter_fn in [
        ("전체", lambda r: True),
        ("doc_page만", lambda r: r["source_type"] == "doc_page"),
        ("action_schema만", lambda r: r["source_type"] == "action_schema"),
    ]:
        print(f"\n=== {label} (RAGAS) ===")
        print(
            f"{'chunk_size':<12}{'faithfulness':<14}{'answer_rel':<12}{'ctx_precision':<15}"
            f"{'ctx_recall':<10}{'ans_correct':<12}{'n':<5}"
        )
        for size in CHUNK_SIZES:
            group = [r for r in rows if r["chunk_size"] == size and filter_fn(r)]
            print(
                f"{size:<12}{_avg(group,'faithfulness'):<14}{_avg(group,'answer_relevancy'):<12}"
                f"{_avg(group,'context_precision'):<15}{_avg(group,'context_recall'):<10}"
                f"{_avg(group,'answer_correctness'):<12}{len(group):<5}"
            )
        print(f"\n=== {label} (검색 직접 지표) ===")
        print(f"{'chunk_size':<12}{'Hit@1':<8}{'Hit@3':<8}{'Hit@5':<8}{'MRR':<8}{'evidence_cov':<14}")
        for size in CHUNK_SIZES:
            group = [r for r in rows if r["chunk_size"] == size and filter_fn(r)]
            print(
                f"{size:<12}{_avg(group,'hit_at_1'):<8}{_avg(group,'hit_at_3'):<8}{_avg(group,'hit_at_5'):<8}"
                f"{_avg(group,'reciprocal_rank'):<8}{_avg(group,'evidence_coverage'):<14}"
            )


if __name__ == "__main__":
    main()
