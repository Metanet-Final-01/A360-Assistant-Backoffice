"""`verifying`(검수-교정 루프) 병목 심화 분석 — 기존 데이터 재가공만으로 수행.

새 계측을 추가하지 않는다. `turn_events`(원격 관측 DB가 정본이지만, 2026-07-19
확인 시점에 원격 DB의 실제 데이터가 문서화된 것보다 훨씬 적고 오래돼 있음
— 아래 참고)와 `llm_usage`/`rag_events`를 로컬 스냅샷 DB에서 읽어 계산만 한다.

⚠️ 왜 로컬 스냅샷 DB를 쓰는가: 2026-07-19 이 스크립트 작성 시점에 원격 관측
DB(Neon)를 직접 조회하니 `turn_events`가 1,454행(7/12 하루치)뿐이고
`rag_events`는 0행이었다 — 여러 세션에 걸쳐 문서화된 상태(turn_events 4,829행,
7/10~7/16, rag_events 36,791행)와 크게 다르다. 반면 로컬 스냅샷 DB
(`a360_observability_test`, 포트 5433 — LOG_SCHEMA_FIXES_2026-07-19.md에 따르면
COPY 프로토콜로 원격을 통째로 복사해둔 것)는 turn_events 4,829행(7/10~7/16),
rag_events 36,793행으로 문서화된 상태와 정확히 일치한다. 즉 **원격 DB가
스냅샷 이후 어떤 이유로든(리셋/복원 등) 데이터가 크게 줄어든 것으로 보이고,
로컬 스냅샷 쪽이 지금은 오히려 더 완전한 이력을 담고 있다.** 이 사실 자체가
이번 분석의 중요한 발견 중 하나라 보고서에도 남긴다 — Backend 팀 확인이
필요한 사안(원격 관측 DB 자체를 건드리는 게 아니라 읽기만 하므로 이 스크립트
실행 자체는 안전하다).

사용:
    python -m scripts.observability_eda.verifying_bottleneck_analysis
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import psycopg

_LOCAL_SANDBOX_DSN = (
    "host=127.0.0.1 port=5433 dbname=a360_observability_test "
    "user=a360_admin password=a360_local_password"
)

_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "local" / "verifying_analysis_2026-07-19"

_VIOLATION_MESSAGE_RE = re.compile(r"^검수 위반 (\d+)건 교정 중$")
_INITIAL_CHECK_MESSAGE = "흐름도 검수 중"
_FINAL_CHECK_MESSAGE = "흐름도 최종 검수 중"


def _fetch_table(table_name: str) -> pd.DataFrame:
    with psycopg.connect(_LOCAL_SANDBOX_DSN) as conn:
        return pd.read_sql(f"SELECT * FROM {table_name}", conn)  # noqa: S608 - table_name은 코드 고정값만


@dataclass
class TurnSummary:
    request_id: str
    session_id: str | None
    total_events: int
    turn_elapsed_ms: int | None  # 마지막 이벤트의 elapsed_ms (턴 전체 소요시간 근사)
    raw_verifying_stage_count: int  # 기존 뷰 방식(메시지 구분 없이 stage='verifying' 카운트)
    correction_rounds: int  # "검수 위반 N건 교정 중" 메시지만 카운트한 진짜 교정 반복 수
    has_initial_check: bool
    has_final_check: bool
    violation_rule_counts: dict[str, int]  # 이 턴에서 나온 모든 violation rule 코드별 건수
    max_violations_in_one_round: int
    stage_after_last_verifying: str | None
    ended_with_error: bool
    ended_with_done: bool


def _parse_violation_rules(detail_text: str | None) -> list[str]:
    if not detail_text:
        return []
    try:
        detail = json.loads(detail_text)
    except (json.JSONDecodeError, TypeError):
        return []
    violations = detail.get("violations") or []
    return [v.get("rule") for v in violations if isinstance(v, dict) and v.get("rule")]


def analyze_turns(turn_events: pd.DataFrame) -> list[TurnSummary]:
    """request_id별로 turn_events를 묶어 검수 루프 지표를 계산한다."""
    summaries: list[TurnSummary] = []
    grouped = turn_events[turn_events["request_id"].notna()].groupby("request_id")

    for request_id, group in grouped:
        rows = group.sort_values("seq").to_dict("records")
        if not rows:
            continue

        session_id = rows[0].get("session_id")
        total_elapsed = rows[-1].get("elapsed_ms")

        raw_verifying_count = sum(1 for r in rows if r.get("stage") == "verifying")
        correction_rounds = 0
        has_initial = False
        has_final = False
        violation_rule_counts: dict[str, int] = defaultdict(int)
        max_violations_in_round = 0
        stage_after_last_verifying: str | None = None

        last_verifying_idx = None
        for idx, r in enumerate(rows):
            if r.get("stage") != "verifying":
                continue
            last_verifying_idx = idx
            message = r.get("message") or ""
            if message == _INITIAL_CHECK_MESSAGE:
                has_initial = True
            elif message == _FINAL_CHECK_MESSAGE:
                has_final = True
            else:
                m = _VIOLATION_MESSAGE_RE.match(message)
                if m:
                    correction_rounds += 1
                    n = int(m.group(1))
                    max_violations_in_round = max(max_violations_in_round, n)
                    for rule in _parse_violation_rules(r.get("detail")):
                        violation_rule_counts[rule] += 1

        if last_verifying_idx is not None and last_verifying_idx + 1 < len(rows):
            stage_after_last_verifying = rows[last_verifying_idx + 1].get("stage") or rows[
                last_verifying_idx + 1
            ].get("kind")

        ended_with_error = any(r.get("kind") == "error" for r in rows)
        ended_with_done = any(r.get("kind") == "done" for r in rows)

        summaries.append(
            TurnSummary(
                request_id=request_id,
                session_id=session_id,
                total_events=len(rows),
                turn_elapsed_ms=total_elapsed,
                raw_verifying_stage_count=raw_verifying_count,
                correction_rounds=correction_rounds,
                has_initial_check=has_initial,
                has_final_check=has_final,
                violation_rule_counts=dict(violation_rule_counts),
                max_violations_in_one_round=max_violations_in_round,
                stage_after_last_verifying=stage_after_last_verifying,
                ended_with_error=ended_with_error,
                ended_with_done=ended_with_done,
            )
        )
    return summaries


def _round_bucket(correction_rounds: int) -> str:
    if correction_rounds == 0:
        return "0회(교정 없음)"
    if correction_rounds == 1:
        return "1회"
    if correction_rounds == 2:
        return "2회"
    return "3회 이상"


def _entered_verifying(s: TurnSummary) -> bool:
    return s.raw_verifying_stage_count > 0


def build_round_bucket_table(summaries: list[TurnSummary]) -> pd.DataFrame:
    """검수(verifying) 단계에 아예 진입한 턴만 대상으로 교정 라운드 수별로 묶는다.

    "0회(교정 없음)"은 verifying에 진입했지만 위반이 하나도 없어 바로 통과한
    턴을 뜻한다 — verifying 자체에 진입 안 한 턴("검수 미진입")과는 다른
    그룹이라 분리했다(전자는 검수 로직이 실제로 돌았고, 후자는 애초에
    ask/qa 등 검수가 필요 없는 턴 유형일 수 있음)."""
    entered = [s for s in summaries if _entered_verifying(s)]
    not_entered = [s for s in summaries if not _entered_verifying(s)]

    rows = []
    by_bucket: dict[str, list[TurnSummary]] = defaultdict(list)
    for s in entered:
        by_bucket[_round_bucket(s.correction_rounds)].append(s)

    order = ["0회(교정 없음)", "1회", "2회", "3회 이상"]
    for bucket in order:
        items = by_bucket.get(bucket, [])
        if not items:
            rows.append({"round_bucket": bucket, "n": 0, "median_ms": None, "p95_ms": None, "mean_ms": None})
            continue
        elapsed = pd.Series([s.turn_elapsed_ms for s in items if s.turn_elapsed_ms is not None])
        rows.append(
            {
                "round_bucket": bucket,
                "n": len(items),
                "median_ms": int(elapsed.median()) if not elapsed.empty else None,
                "p95_ms": int(elapsed.quantile(0.95)) if not elapsed.empty else None,
                "mean_ms": int(elapsed.mean()) if not elapsed.empty else None,
            }
        )

    # 참고용 — verifying에 아예 진입 안 한 턴(다른 종류의 턴일 가능성, 비교 기준선)
    if not_entered:
        elapsed = pd.Series([s.turn_elapsed_ms for s in not_entered if s.turn_elapsed_ms is not None])
        rows.append(
            {
                "round_bucket": "(참고) 검수 미진입",
                "n": len(not_entered),
                "median_ms": int(elapsed.median()) if not elapsed.empty else None,
                "p95_ms": int(elapsed.quantile(0.95)) if not elapsed.empty else None,
                "mean_ms": int(elapsed.mean()) if not elapsed.empty else None,
            }
        )
    return pd.DataFrame(rows)


def build_stage_transition_table(summaries: list[TurnSummary]) -> pd.DataFrame:
    counts: dict[str, int] = defaultdict(int)
    for s in summaries:
        key = s.stage_after_last_verifying or "(다음 이벤트 없음/턴 끝)"
        counts[key] += 1
    return pd.DataFrame(
        sorted(counts.items(), key=lambda kv: -kv[1]), columns=["next_stage_or_kind", "count"]
    )


def deep_dive_three_plus(
    summaries: list[TurnSummary], llm_usage: pd.DataFrame, rag_events: pd.DataFrame
) -> dict:
    """3회 이상 교정 반복된 턴들의 공통점: violation rule 분포, request_id 기준
    llm_usage/rag_events 조인 가능 여부와 조인됐을 때의 model/RAG 사용 여부."""
    three_plus = [s for s in summaries if s.correction_rounds >= 3]

    rule_counts: dict[str, int] = defaultdict(int)
    for s in three_plus:
        for rule, cnt in s.violation_rule_counts.items():
            rule_counts[rule] += cnt

    request_ids = {s.request_id for s in three_plus}
    llm_join = llm_usage[llm_usage["request_id"].isin(request_ids)]
    rag_join = rag_events[rag_events["request_id"].isin(request_ids)]

    joined_request_ids = set(llm_join["request_id"].unique()) | set(rag_join["request_id"].unique())

    return {
        "n_turns": len(three_plus),
        "violation_rule_distribution": dict(sorted(rule_counts.items(), key=lambda kv: -kv[1])),
        "request_ids_with_any_llm_or_rag_join": len(joined_request_ids),
        "request_ids_total": len(request_ids),
        "join_rate": round(len(joined_request_ids) / len(request_ids), 3) if request_ids else None,
        "models_seen_in_joined_llm_usage": (
            llm_join["model"].value_counts().to_dict() if not llm_join.empty else {}
        ),
        "components_seen_in_joined_llm_usage": (
            llm_join["component"].value_counts().to_dict() if not llm_join.empty else {}
        ),
        "rag_event_types_seen": (
            rag_join["event"].value_counts().to_dict() if not rag_join.empty else {}
        ),
    }


def success_vs_error_split(summaries: list[TurnSummary]) -> pd.DataFrame:
    rows = []
    for label, predicate in [
        ("성공(에러 없음)", lambda s: not s.ended_with_error),
        ("에러 발생", lambda s: s.ended_with_error),
    ]:
        items = [s for s in summaries if predicate(s)]
        elapsed = pd.Series([s.turn_elapsed_ms for s in items if s.turn_elapsed_ms is not None])
        rows.append(
            {
                "group": label,
                "n": len(items),
                "median_ms": int(elapsed.median()) if not elapsed.empty else None,
                "p95_ms": int(elapsed.quantile(0.95)) if not elapsed.empty else None,
                "avg_correction_rounds": (
                    round(sum(s.correction_rounds for s in items) / len(items), 2) if items else None
                ),
            }
        )
    return pd.DataFrame(rows)


def eval_vs_user_note(turn_events: pd.DataFrame) -> str:
    """turn_events 자체가 eval 스크립트 트래픽을 포함하는지 확인.

    goldset_eval 스크립트(run_eval.py/probe_eval.py)는 app.agent.v3.recommend()를
    직접 호출하고 app/api/sessions.py의 SSE 핸들러(_tev)를 거치지 않는다 —
    turn_events는 오직 그 SSE 핸들러 안에서만 기록된다(이번 세션 앞선 조사에서
    코드로 확인함). 따라서 turn_events에 있는 행은 구조적으로 eval 스크립트
    트래픽일 수 없다 — 이 함수는 그 전제가 실제 데이터와 모순되지 않는지만
    가볍게 재확인한다(예: session_id가 전부 NULL인 이상 패턴이 있는지)."""
    null_session = turn_events["session_id"].isna().sum()
    total = len(turn_events)
    return (
        f"turn_events 전체 {total}행 중 session_id NULL {null_session}행"
        f"({round(null_session / total * 100, 1) if total else 0}%). "
        "eval 스크립트는 app.agent.v3.recommend()를 직접 호출해 app/api/sessions.py의 "
        "SSE 핸들러(_tev)를 거치지 않으므로 turn_events에 원천적으로 안 남는다 "
        "(코드로 확인됨, 이번 분석에서 재검증 안 함) — 즉 이 표의 모든 턴은 "
        "구조적으로 실사용자 또는 내부 auto-compact 트래픽이다."
    )


def main() -> None:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    turn_events = _fetch_table("turn_events")
    llm_usage = _fetch_table("llm_usage")
    rag_events = _fetch_table("rag_events")

    summaries = analyze_turns(turn_events)

    round_table = build_round_bucket_table(summaries)
    transition_table = build_stage_transition_table(summaries)
    deep_dive = deep_dive_three_plus(summaries, llm_usage, rag_events)
    success_error_table = success_vs_error_split(summaries)
    eval_note = eval_vs_user_note(turn_events)

    round_table.to_csv(_OUTPUT_DIR / "round_bucket_table.csv", index=False)
    transition_table.to_csv(_OUTPUT_DIR / "stage_transition_table.csv", index=False)
    success_error_table.to_csv(_OUTPUT_DIR / "success_vs_error_table.csv", index=False)
    with (_OUTPUT_DIR / "three_plus_deep_dive.json").open("w", encoding="utf-8") as f:
        json.dump(deep_dive, f, ensure_ascii=False, indent=2)
    with (_OUTPUT_DIR / "eval_vs_user_note.txt").open("w", encoding="utf-8") as f:
        f.write(eval_note)

    # request_id별 원자료도 남긴다(재현/재검토용) — dataclass를 dict로.
    raw_rows = [
        {
            **s.__dict__,
            "round_bucket": _round_bucket(s.correction_rounds),
        }
        for s in summaries
    ]
    pd.DataFrame(raw_rows).to_csv(_OUTPUT_DIR / "per_turn_raw.csv", index=False)

    print("=== 검수 라운드별 표본/중앙값/p95 ===")
    print(round_table.to_string(index=False))
    print()
    print("=== verifying 이후 다음 stage/kind 분포 ===")
    print(transition_table.to_string(index=False))
    print()
    print("=== 3회 이상 교정 반복 턴 딥다이브 ===")
    print(json.dumps(deep_dive, ensure_ascii=False, indent=2))
    print()
    print("=== 성공 vs 에러 그룹 비교 ===")
    print(success_error_table.to_string(index=False))
    print()
    print("=== eval vs user 트래픽 노트 ===")
    print(eval_note)
    print()
    print(f"전체 턴(request_id) 수: {len(summaries)}")
    print(f"결과 저장 위치: {_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
