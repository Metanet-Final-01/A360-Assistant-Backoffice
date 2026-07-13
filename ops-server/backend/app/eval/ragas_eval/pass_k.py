"""RAGAS 지표에 pass@k(Codex 논문 — Chen et al. 2021, arXiv:2107.03374) 적용.

bfcl_eval.pass_k와 같은 발상·같은 공식(pass@k = 1 - C(n-c, k) / C(n, k))이지만, RAGAS
지표(faithfulness/answer_relevancy/context_precision/context_recall)는 BFCL의
ast_match처럼 이미 이진값이 아니라 0~1 연속값이다 — pass@k 자체가 "통과냐 아니냐"를
n번 반복에서 세는 지표라, 연속값을 그대로 넣을 수 없고 먼저 "통과" 기준값(threshold)으로
이진화해야 한다.

임계값 0.7: RAGAS 공식 문서·커뮤니티 가이드가 흔히 쓰는 "양호" 기준선(절대적으로 정해진
값은 아님 — 데이터셋마다 다를 수 있어 상수로 노출해 나중에 조정 가능하게 함). 케이스
하나의 "통과"는 4개 지표 전부 이 기준을 넘겨야 한다고 정의한다(BFCL의 ast_match가 이름+
파라미터 전부 만족해야 하는 것과 같은 결로, "부분적으로만 좋음"을 통과로 안 침).
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from .runner import _append_log, _to_metrics, run_ragas_eval

logger = logging.getLogger(__name__)

# "통과" 판정 임계값 — 근거는 모듈 docstring 참고. 필요해지면 여기만 바꾸면 된다.
PASS_THRESHOLD = 0.7

state: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "n_repeats": 0, "completed_repeats": 0, "evaluation_id": None, "error": None, "log": [],
}


def reserve() -> bool:
    if state["running"]:
        return False
    state.update({
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "n_repeats": 0, "completed_repeats": 0,
        "evaluation_id": None, "error": None, "log": [],
    })
    return True


def _pass_at_k(n: int, c: int, k: int) -> float:
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def _case_passed(result) -> bool | None:
    """4개 지표 전부 PASS_THRESHOLD 이상이면 통과. 검색 실패 등으로 지표 자체가 없으면
    (faithfulness가 None) 채점 불가 — None을 반환해 그 반복은 outcome 집계에서 제외한다
    (실패를 "미통과"로 세면 인프라 오류와 실제 품질 저하가 섞여 신호가 흐려짐)."""
    values = [result.faithfulness, result.answer_relevancy, result.context_precision, result.context_recall]
    if any(v is None for v in values):
        return None
    return all(v >= PASS_THRESHOLD for v in values)


def execute_pass_k_and_save(agent_label: str, n_repeats: int, judge_model: str = "gpt-4o-mini") -> None:
    """reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다. bfcl_eval.pass_k와
    동일 구조 — 매 반복의 개별 결과(source="ragas")를 그대로 저장하고, 케이스별
    pass@k 집계(source="ragas_pass_k")도 같은 evaluation_id로 저장한다."""
    evaluation_id = uuid4().hex[:12]
    state.update({"n_repeats": n_repeats, "evaluation_id": evaluation_id})
    try:
        per_case_outcomes: dict[str, list[bool]] = defaultdict(list)

        for i in range(n_repeats):
            _append_log(state["log"], f"=== 반복 {i + 1}/{n_repeats} 시작 ===")
            results = run_ragas_eval(
                judge_model=judge_model,
                on_progress=lambda msg, rep=i: _append_log(state["log"], f"[반복 {rep + 1}/{n_repeats}] {msg}"),
            )
            for r in results:
                record = EvalRunRecord(
                    evaluation_id=evaluation_id, case_id=r.case_id, source="ragas",
                    agent_label=agent_label, config={"repeat_index": i, "n_repeats": n_repeats},
                    metrics=_to_metrics(r),
                    raw={
                        "question": r.question, "retrieved_contexts": r.retrieved_contexts,
                        "retrieved_doc_ids": r.retrieved_doc_ids, "reference_doc_ids": r.reference_doc_ids,
                        "response": r.response, "ground_truth": r.ground_truth, "error": r.error,
                    },
                )
                append_run(record)
                passed = _case_passed(r)
                if passed is not None:
                    per_case_outcomes[r.case_id].append(passed)
            state["completed_repeats"] = i + 1

        for case_id, outcomes in per_case_outcomes.items():
            n = len(outcomes)
            if n == 0:
                continue
            c = sum(1 for ok in outcomes if ok)
            metrics = [EvalMetric(name="pass_at_1", value=c / n)]
            for k in (3, 5):
                if k <= n:
                    metrics.append(EvalMetric(name=f"pass_at_{k}", value=_pass_at_k(n, c, k)))
            append_run(EvalRunRecord(
                evaluation_id=evaluation_id, case_id=case_id, source="ragas_pass_k",
                agent_label=agent_label, metrics=metrics,
                raw={"n": n, "c": c, "outcomes": outcomes, "pass_threshold": PASS_THRESHOLD},
            ))
    except Exception as e:  # noqa: BLE001 — 백그라운드 태스크 예외를 상태로 남겨야 프론트가 안다
        logger.exception("RAGAS pass@k 실행 실패")
        state["error"] = str(e)
    finally:
        state.update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})
