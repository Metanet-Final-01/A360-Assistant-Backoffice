"""critical attribute 비교기. GPT 재설계안의 구분을 그대로 따른다:

정적 규칙으로 확실히 비교 가능한 것 (STATIC):
  URL(frame_src) 정규화 비교, widget_action 정확 일치, css_selector/dom_xpath
  정확 일치, loop_type 정확 일치, loop 대상(iterator/condition 변수명) 정확 일치.

정적 규칙만으로는 동치 판정이 애매한 것 (LLM_JUDGE 필요, judge.py로 위임):
  - 서로 다른 selector가 같은 객체를 가리키는지
  - 서로 다른 액션 조합이 같은 의도인지
  둘 다 여기서는 "정적으로 다르다고 나오면 UNCERTAIN으로 표시하고 넘긴다"까지만
  하고, 실제 LLM 호출은 judge.py의 별도 함수를 호출하는 쪽에서 한다 - 이 모듈 자체는
  항상 정적 판정만 하고 애매하면 애매하다고 정직하게 보고한다(임의로 점수화해서
  하나의 숫자로 뭉개지 않는다 - GPT가 지적한 "근거 없는 종합점수 금지"를 지킨다)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["match", "mismatch", "uncertain", "not_applicable"]


@dataclass
class FieldComparison:
    field: str
    verdict: Verdict
    gold_value: object
    pred_value: object
    note: str = ""


@dataclass
class CriticalAttributeReport:
    fields: list[FieldComparison] = field(default_factory=list)

    def add(self, name: str, verdict: Verdict, gold_value, pred_value, note: str = "") -> None:
        self.fields.append(FieldComparison(name, verdict, gold_value, pred_value, note))

    def summary_counts(self) -> dict[str, int]:
        counts = {"match": 0, "mismatch": 0, "uncertain": 0, "not_applicable": 0}
        for f in self.fields:
            counts[f.verdict] += 1
        return counts


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip().lower()
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    return u.rstrip("/")


def compare_recorder(gold_sig: dict, pred_sig: dict) -> CriticalAttributeReport:
    report = CriticalAttributeReport()
    gold_target = gold_sig.get("target") or {}
    pred_target = pred_sig.get("target") or {}

    ga, pa = gold_sig.get("widget_action"), pred_sig.get("widget_action")
    report.add("widget_action", "match" if ga == pa else "mismatch", ga, pa)

    gu, pu = _normalize_url(gold_target.get("frame_src")), _normalize_url(pred_target.get("frame_src"))
    report.add("frame_src(website)", "match" if gu == pu else "mismatch", gold_target.get("frame_src"), pred_target.get("frame_src"))

    gs, ps = gold_target.get("css_selector"), pred_target.get("css_selector")
    if gs == ps:
        report.add("css_selector", "match", gs, ps)
    else:
        # selector 문자열이 다르다고 해서 반드시 다른 객체는 아니다(같은 요소를
        # 가리키는 표현이 여러 개일 수 있음) - 이건 정적 규칙으로 확정 못 하므로
        # LLM judge에게 넘긴다는 뜻으로 uncertain 처리.
        report.add("css_selector", "uncertain", gs, ps, note="문자열이 다름 - 같은 객체인지는 judge.py의 judge_selector_equivalence()로 확인 필요")

    gx, px = gold_target.get("dom_xpath"), pred_target.get("dom_xpath")
    report.add("dom_xpath", "match" if gx == px else "uncertain", gx, px)

    return report


def compare_loop(gold_sig: dict, pred_sig: dict) -> CriticalAttributeReport:
    report = CriticalAttributeReport()

    gt, pt = gold_sig.get("loop_type"), pred_sig.get("loop_type")
    if gt is None or pt is None:
        report.add("loop_type", "not_applicable", gt, pt, note="loop_type 정보 없는 구조적 노드(anchor 등)")
        return report
    report.add("loop_type", "match" if gt == pt else "mismatch", gt, pt)

    gtarget, ptarget = gold_sig.get("target") or {}, pred_sig.get("target") or {}
    if gtarget.get("kind") != ptarget.get("kind"):
        report.add("loop_target_kind", "mismatch", gtarget.get("kind"), ptarget.get("kind"))
    else:
        gname, pname = gtarget.get("name"), ptarget.get("name")
        report.add("loop_target_name", "match" if gname == pname else "mismatch", gname, pname)

    return report


def compare_common(gold_common: dict, pred_common: dict) -> CriticalAttributeReport:
    """서로 다른 패키지끼리도(Recorder vs WebAutomation 등) 비교 가능한
    attribute_signature.common_signature()의 {operation, target_text} 비교.
    Rule/Judge로 매칭된 액션 쌍에 대해서만 호출한다 - 매칭 안 된 액션에는 안 씀."""
    report = CriticalAttributeReport()

    go, po = gold_common.get("operation"), pred_common.get("operation")
    if go is None or po is None:
        report.add("operation", "not_applicable", go, po, note="operation 정보 없는 패키지")
    else:
        report.add("operation", "match" if go == po else "mismatch", go, po)

    gt, pt = gold_common.get("target_text"), pred_common.get("target_text")
    if not gt or not pt:
        # 한쪽이라도 값이 없으면 mismatch로 잘못 떨어뜨리지 않고 not_applicable -
        # 빈 문자열/None과 실제 값을 비교하는 건 의미가 없음.
        report.add("target_text", "not_applicable", gt, pt)
    elif gt == pt:
        report.add("target_text", "match", gt, pt)
    else:
        report.add("target_text", "uncertain", gt, pt, note="문자열이 다름 - 같은 대상을 가리키는 다른 표현일 수 있음, judge로 확인 필요")

    return report


def compare_critical_attributes(step_type: str, gold_sig: dict, pred_sig: dict) -> CriticalAttributeReport | None:
    """step_type("action"의 package나 "loop" 같은 구조 타입)에 맞는 비교기로 위임.
    두 signature 다 비어있으면(critical attribute 없는 일반 패키지) None - 이 경우
    지금까지 하던 package.action 정확매칭만으로 채점하면 된다는 뜻."""
    if not gold_sig and not pred_sig:
        return None
    if "widget_action" in gold_sig or "widget_action" in pred_sig:
        return compare_recorder(gold_sig, pred_sig)
    if "loop_type" in gold_sig or "loop_type" in pred_sig:
        return compare_loop(gold_sig, pred_sig)
    return None
