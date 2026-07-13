"""BFCL 방식 액션 호출 평가 실행기.

app/eval/ragas_eval/runner.py와 같은 발상 — 인프로세스로 돌고(서브프로세스 없음),
A360-Assistant-Backend의 실제 API(POST /api/sessions, POST /api/sessions/{id}/turn)를
그대로 호출한다. 별도 채점 로직을 새로 짜지 않고 실제 서비스 경로를 그대로 태운다.

/turn은 SSE라 스트림을 끝까지 읽어야 한다 — "verifying" 단계 이벤트에 R1~R8 정적 검수
위반(checker.py)이, "done" 이벤트에 최종 추천(package/action/parameters 트리)이 있다.
둘 다 DB에는 안 남는 값이라(turn_events는 카운트만 저장) 여기서 직접 캡처해야 한다.

카테고리별 채점(schema.py 참고)은 BFCL 논문의 평가방식 표를 그대로 따른다 —
simple/multiple은 AST Substring Matching(파라미터 값이 SET에 속하는가), missing_*는
"critical 정보 없이 확정하지 않는가", multi_turn_state는 후속 턴 편집 후 최종 상태,
response_based는 선행관계(R7 세션 생명주기) 위반 여부다.
"""

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from ..log_schema import EvalMetric, EvalRunRecord
from ..log_store import append_run
from .schema import BFCLCase, BFCLCaseResult, BFCLTurn, BFCLTurnResult, ExpectedTarget

logger = logging.getLogger(__name__)

_CASES_PATH = Path(__file__).resolve().parent / "cases" / "goldset_v1.json"

_MAX_LOG_LINES = 200

state: dict = {
    "running": False, "started_at": None, "finished_at": None, "saved": 0, "cases": 0,
    "error": None, "log": [],
}


def reserve() -> bool:
    """RAGAS runner와 동일한 원자적 check-and-set — 동시 실행 경합 제거."""
    if state["running"]:
        return False
    state.update({"running": True, "started_at": datetime.now(timezone.utc).isoformat(),
                  "finished_at": None, "saved": 0, "cases": 0, "error": None, "log": []})
    return True


def _append_log(log: list[str], message: str) -> None:
    """실행 중 케이스별 진행 상황을 state에 쌓는다 — 프론트가 폴링하며 그대로 보여준다
    (Streamlit은 진짜 SSE 스트리밍을 못 받으니, 짧은 주기 폴링 + 누적 로그로 "실시간처럼"
    보이게 하는 현실적인 타협). 무한정 쌓이지 않게 최근 N줄만 유지."""
    log.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}")
    del log[:-_MAX_LOG_LINES]


class BFCLGoldsetError(RuntimeError):
    """골드셋 파일이 없거나 비어 있음."""


def load_cases() -> list[BFCLCase]:
    if not _CASES_PATH.exists():
        raise BFCLGoldsetError(f"골드셋 파일이 없습니다: {_CASES_PATH}")
    raw = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    cases = [BFCLCase.model_validate(c) for c in raw]
    if not cases:
        raise BFCLGoldsetError(f"골드셋이 비어 있습니다: {_CASES_PATH}")
    return cases


def _iter_actions(actions: list[dict]):
    """RecommendedAction 트리를 평평하게 순회한다 (schemas/recommendation.py의
    iter_actions()와 같은 개념 — 여기서는 API 응답 dict를 그대로 순회)."""
    for a in actions:
        yield a
        for child in a.get("children") or []:
            yield from _iter_actions([child])


def _find_actions(recommendation: dict | None, package: str, action: str) -> list[dict]:
    """트리에서 (package, action)이 일치하는 액션을 전부 찾는다(하나가 아니라 리스트) —
    Excel/Excel_MS 실측에서 확인됐듯, 애매한 요청 하나에 같은 액션을 파라미터만 바꿔
    여러 개 만드는 경우가 실제로 있다(예: 특정 셀/활성 셀 두 버전을 한 번에 제안)."""
    if not recommendation:
        return []
    found = []
    for step in recommendation.get("steps") or []:
        for a in _iter_actions(step.get("actions") or []):
            if a.get("package") == package and a.get("action") == action:
                found.append(a)
    return found


def _has_any_action(recommendation: dict | None) -> bool:
    if not recommendation:
        return False
    for step in recommendation.get("steps") or []:
        for _ in _iter_actions(step.get("actions") or []):
            return True
    return False


def _param_value(action: dict, name: str):
    for p in action.get("parameters") or []:
        if p.get("name") == name:
            return p.get("value")
    return None


def _check_param(action: dict, check) -> bool:
    """BFCL AST Substring Matching(논문 463~472줄): 파라미터 값이 하나의 고정 문자열과
    완전히 일치할 필요 없이 '미리 정의된 유효값 집합'에 속하는지를 본다 — exact/contains의
    expected가 리스트면 그 중 하나만 만족해도 통과."""
    value = _param_value(action, check.name)
    if check.check == "nonempty":
        return value is not None and str(value).strip() != ""
    if check.check == "bool_true":
        return value is True or str(value).lower() == "true"
    if check.check == "bool_false":
        return value is False or str(value).lower() == "false"
    if value is None:
        return False
    allowed = check.expected if isinstance(check.expected, list) else [check.expected]
    if check.check == "exact":
        return str(value).strip() in {str(a).strip() for a in allowed}
    if check.check == "contains":
        return any(str(a) in str(value) for a in allowed)
    if check.check == "enum":
        return str(value) in {str(a) for a in allowed}
    return False


def _is_param_empty(action: dict, name: str) -> bool:
    value = _param_value(action, name)
    return value is None or str(value).strip() == ""


def _score_target(recommendation: dict | None, target: ExpectedTarget, violations: list[dict]):
    """target 하나에 대해 트리에서 가장 잘 맞는 occurrence를 찾아 채점한다.

    같은 (package, action)이 여럿이면(Multiple 실행 등) 파라미터를 가장 많이 만족하는
    occurrence를 고른다 — "이름은 맞혔는데 그 중 아무 것도 완전히 못 맞혔다"를
    "이름도 못 맞혔다"와 구분하기 위함.
    """
    occurrences = _find_actions(recommendation, target.package, target.action)
    if not occurrences:
        return False, False, None, {}
    best_action, best_results, best_score = occurrences[0], {}, -1
    for occ in occurrences:
        results = {c.name: _check_param(occ, c) for c in target.params}
        score = sum(results.values())
        if score > best_score:
            best_action, best_results, best_score = occ, results, score
    ast_match = all(best_results.values())
    if target.require_no_prereq_violation:
        prereq_ok = not any(
            v.get("rule") in ("R7", "R8")
            and v.get("package") == target.package
            and v.get("action") == target.action
            for v in violations
        )
        ast_match = ast_match and prereq_ok
    return True, ast_match, best_action, best_results


def _score_turn(recommendation: dict | None, turn: BFCLTurn, actual_type: str | None,
                 violations: list[dict]) -> BFCLTurnResult:
    result = BFCLTurnResult(message=turn.message, actual_type=actual_type, violations=violations)

    if turn.expect_no_action:
        # Irrelevance/Missing Functions 공통: 액션을 지어내지만 않으면 정답 —
        # type이 recommendation이 아니거나, recommendation이어도 실제 액션이 하나도 없으면 통과.
        ok = actual_type != "recommendation" or not _has_any_action(recommendation)
        result.name_match = ok
        result.ast_match = ok
        return result

    matched_index, best = None, None  # best = (ast_match, name_match, param_score, action, results)
    for i, target in enumerate(turn.expected_targets):
        name_match, ast_match, action, results = _score_target(recommendation, target, violations)
        if not name_match:
            continue
        score = (ast_match, sum(results.values()) if results else 0)
        if best is None or score > (best[0], best[2]):
            matched_index, best = i, (ast_match, action, sum(results.values()) if results else 0, results)

    if best is None:
        # Missing Parameters(논문 236~242줄)는 액션을 아예 안 만든 것도 "critical 정보
        # 없이 확정하지 않음"이라는 같은 정답으로 친다 — schema.py 설계 의도인데
        # 처음 구현에서 빠뜨렸다가 datetime_add_minutes 실측(steps 비어있는데 실패로
        # 채점됨)에서 발견해 고침. missing_functions/irrelevance는 expect_no_action
        # 분기에서 이미 처리되므로 여기 오는 건 missing_parameters뿐이다.
        ok = bool(turn.missing_params_expected_empty)
        result.name_match = ok
        result.ast_match = ok
        return result

    ast_match, action, _score, param_results = best
    result.matched_target_index = matched_index
    result.name_match = True
    result.actual_package = action.get("package")
    result.actual_action = action.get("action")
    result.actual_params = {p.get("name"): p.get("value") for p in action.get("parameters") or []}
    result.param_results = param_results

    if turn.missing_params_expected_empty:
        # Missing Parameters(논문 236~242줄): critical 파라미터를 추론할 수 없으면
        # 비워둬야 정답 — 값을 지어냈으면(hallucination) 실패.
        stayed_empty = all(_is_param_empty(action, name) for name in turn.missing_params_expected_empty)
        result.ast_match = ast_match and stayed_empty
    else:
        result.ast_match = ast_match
    return result


_RECOMMEND_TRIGGER_HINT = (
    "문서 없는 빈 세션에 채팅만 보내면 라우터가 recommendation 대신 qa(설명)로 판단하거나, "
    "recommendation으로 판단해도 백엔드가 저장 시 파싱된 문서를 요구해 예외로 끝난다(실측 확인: "
    "`분석 결과를 저장할 파싱 완료 문서가 없습니다`). document_text가 있는 케이스는 그 문서를 "
    "먼저 등록한 뒤 turns[0].message로 고정 트리거 문구를 보내야 한다 — 골드셋 작성 시 지켜야 함."
)


def _stream_turn(client: httpx.Client, backend_url: str, session_id: str, message: str) -> dict:
    """POST /turn을 스트리밍으로 읽어 verifying 단계 violations + done 이벤트를 모은다."""
    violations: list[dict] = []
    done_data: dict | None = None
    with client.stream(
        "POST", f"{backend_url}/api/sessions/{session_id}/turn",
        json={"message": message, "operation": "chat"}, timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            evt = json.loads(line[len("data: "):])
            if evt.get("event") == "stage" and evt.get("stage") == "verifying":
                v = (evt.get("data") or {}).get("violations")
                if v:
                    violations = v
            elif evt.get("event") == "done":
                done_data = evt.get("data") or {}
    return {"violations": violations, "done": done_data}


def run_bfcl_eval(
    backend_url: str | None = None, on_progress: Callable[[str], None] | None = None,
) -> list[BFCLCaseResult]:
    """on_progress: 케이스 하나 끝날 때마다 사람이 읽을 진행 메시지 한 줄을 받는 콜백
    (실시간 로그 표시용, execute_and_save/pass_k.py가 각자 state["log"]에 쌓는다)."""
    backend_url = (backend_url or os.getenv("A360_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
    cases = load_cases()
    results: list[BFCLCaseResult] = []

    with httpx.Client() as client:
        for i, case in enumerate(cases, 1):
            try:
                session = client.post(f"{backend_url}/api/sessions", json={}, timeout=10.0)
                session.raise_for_status()
                session_id = session.json()["session_id"]

                if case.document_text is not None:
                    doc = client.post(
                        f"{backend_url}/api/documents/text",
                        json={"text": case.document_text, "session_id": session_id}, timeout=10.0,
                    )
                    doc.raise_for_status()

                turn_results: list[BFCLTurnResult] = []
                for turn in case.turns:
                    stream_result = _stream_turn(client, backend_url, session_id, turn.message)
                    done = stream_result["done"] or {}
                    turn_results.append(_score_turn(
                        done.get("recommendation"), turn, done.get("type"), stream_result["violations"],
                    ))

                question = case.document_text or case.turns[0].message
                name_match = all(t.name_match for t in turn_results)
                ast_match = all(t.ast_match for t in turn_results)
                results.append(BFCLCaseResult(
                    case_id=case.case_id, category=case.category, question=question,
                    turns=turn_results, name_match=name_match, ast_match=ast_match,
                ))
                if on_progress:
                    mark = "✓" if ast_match else ("△" if name_match else "✗")
                    on_progress(f"[{i}/{len(cases)}] {mark} {case.case_id} ({case.category})")
            except Exception as e:  # noqa: BLE001 — 케이스 하나 실패가 전체를 막지 않는다
                logger.warning("BFCL 케이스 실패: %s", case.case_id, exc_info=True)
                results.append(BFCLCaseResult(
                    case_id=case.case_id, category=case.category,
                    question=case.document_text or (case.turns[0].message if case.turns else ""),
                    error=str(e),
                ))
                if on_progress:
                    on_progress(f"[{i}/{len(cases)}] ⚠ {case.case_id} 오류: {e}")

    return results


def _to_metrics(result: BFCLCaseResult) -> list[EvalMetric]:
    param_checks = [ok for t in result.turns for ok in t.param_results.values()]
    param_acc = sum(param_checks) / len(param_checks) if param_checks else None
    violation_count = sum(len(t.violations) for t in result.turns)
    pairs = [
        ("bfcl_name_match", 1.0 if result.name_match else 0.0),
        ("bfcl_ast_match", 1.0 if result.ast_match else 0.0),
        ("bfcl_param_accuracy", param_acc),
        ("bfcl_violation_count", float(violation_count)),
    ]
    return [EvalMetric(name=name, value=value) for name, value in pairs if value is not None]


def execute_and_save(agent_label: str) -> None:
    """reserve()가 이미 running=True로 바꿔놨다는 전제로 호출된다."""
    try:
        results = run_bfcl_eval(on_progress=lambda msg: _append_log(state["log"], msg))
        evaluation_id = uuid4().hex[:12]
        saved = 0
        for r in results:
            record = EvalRunRecord(
                evaluation_id=evaluation_id,
                case_id=r.case_id, source="bfcl", agent_label=agent_label,
                metrics=_to_metrics(r),
                raw={
                    "category": r.category,
                    "question": r.question,
                    "name_match": r.name_match, "ast_match": r.ast_match,
                    "turns": [t.model_dump() for t in r.turns],
                    "error": r.error,
                },
            )
            append_run(record)
            saved += 1
        state.update({"saved": saved, "cases": len(results)})
    except Exception as e:  # noqa: BLE001 — 백그라운드 태스크 예외를 상태로 남겨야 프론트가 안다
        logger.exception("BFCL 평가 실행 실패")
        state["error"] = str(e)
    finally:
        state.update({"running": False, "finished_at": datetime.now(timezone.utc).isoformat()})
