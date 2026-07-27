"""pass@k(Codex 논문 — Chen et al. 2021, "Evaluating Large Language Models Trained on
Code", arXiv:2107.03374) 반복 일관성 평가.

이 세션에서 실제로 관찰된 문제가 이 기능의 직접적인 동기다: `browser_open_newtab`
케이스가 완전히 같은 입력·같은 골드셋으로 한 번은 통과(url 정상 추출), 한 번은
실패(url이 about:blank로 채워짐)했다 — 단발 실행 결과만으로는 그게 실제 경향인지
우연인지 구분할 수 없었다. 같은 케이스를 n번 반복 실행해 c번 통과했을 때, "k번
시도 중 하나라도 맞을 확률"의 비편향 추정치를 계산한다:

    pass@k = 1 - C(n-c, k) / C(n, k)

naive하게 `1 - (1 - c/n)^k`를 쓰면 복원추출(매번 독립 시행)을 가정해 과소추정된다
— 원 논문이 비복원추출 U-statistic으로 이를 보정한 이유이자, 그대로 가져온 이유.
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from .reservation import finish_state, reserve_state
from .runner import _append_log, _to_metrics, run_bfcl_eval

logger = logging.getLogger(__name__)

state: dict = {
    "running": False, "started_at": None, "finished_at": None,
    "n_repeats": 0, "completed_repeats": 0, "evaluation_id": None, "error": None, "log": [],
}


def reserve() -> bool:
    """runner.reserve()와 동일한 원자적 check-and-set."""
    return reserve_state(state, {
        "running": True, "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "n_repeats": 0, "completed_repeats": 0,
        "evaluation_id": None, "error": None, "log": [],
    })


def _pass_at_k(n: int, c: int, k: int) -> float:
    """k > n - c(실패 횟수)보다 크면 k번 중 전부 실패로 뽑을 조합 자체가 없으므로
    확정 통과(1.0) — comb(a, b)는 a < b일 때 0을 반환하니 수식 그대로도 맞지만
    의도를 명확히 하려고 분기했다."""
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def execute_pass_k_and_save(agent_label: str, n_repeats: int) -> None:
    """reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다. 전체 골드셋을
    n_repeats번 반복 실행해 매 반복의 개별 결과(source="bfcl", config.repeat_index로
    구분)를 그대로 저장하고 — 기존 단일 실행 결과 조회와 완전히 같은 방식으로 볼 수
    있게 — 케이스별 pass@k 집계(source="bfcl_pass_k")도 같은 evaluation_id로 저장한다.
    """
    evaluation_id = uuid4().hex[:12]
    state.update({"n_repeats": n_repeats, "evaluation_id": evaluation_id})
    try:
        per_case_outcomes: dict[str, list[bool]] = defaultdict(list)
        per_case_category: dict[str, str] = {}

        for i in range(n_repeats):
            _append_log(state["log"], f"=== 반복 {i + 1}/{n_repeats} 시작 ===")
            results = run_bfcl_eval(
                on_progress=lambda msg, rep=i: _append_log(state["log"], f"[반복 {rep + 1}/{n_repeats}] {msg}")
            )
            for r in results:
                record = EvalRunRecord(
                    evaluation_id=evaluation_id, case_id=r.case_id, source="bfcl",
                    agent_label=agent_label, config={"repeat_index": i, "n_repeats": n_repeats},
                    metrics=_to_metrics(r),
                    raw={
                        "category": r.category, "question": r.question,
                        "name_match": r.name_match, "ast_match": r.ast_match,
                        "turns": [t.model_dump() for t in r.turns], "error": r.error,
                    },
                )
                append_run(record)
                per_case_outcomes[r.case_id].append(r.ast_match)
                per_case_category[r.case_id] = r.category
            state["completed_repeats"] = i + 1

        for case_id, outcomes in per_case_outcomes.items():
            n = len(outcomes)
            c = sum(1 for ok in outcomes if ok)
            metrics = [EvalMetric(name="pass_at_1", value=c / n)]
            for k in (3, 5):
                if k <= n:
                    metrics.append(EvalMetric(name=f"pass_at_{k}", value=_pass_at_k(n, c, k)))
            append_run(EvalRunRecord(
                evaluation_id=evaluation_id, case_id=case_id, source="bfcl_pass_k",
                agent_label=agent_label, metrics=metrics,
                raw={"category": per_case_category[case_id], "n": n, "c": c, "outcomes": outcomes},
            ))
    except Exception as e:  # noqa: BLE001 — 백그라운드 태스크 예외를 상태로 남겨야 프론트가 안다
        logger.exception("BFCL pass@k 실행 실패")
        state["error"] = str(e)
    finally:
        finish_state(state)
