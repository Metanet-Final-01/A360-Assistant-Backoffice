"""정적 규칙만으로는 동치 판정이 안 되는 경계에서만 쓰는 LLM judge.

로컬 Exaone 대신 gpt-4o-mini를 쓴다(사용자 지시, 2026-07-30) - API 키는
A360-Assistant-Backend-eval-clean/.env의 OPENAI_API_KEY를 읽어서 쓰고 값 자체는
절대 출력하지 않는다.

GPT 재설계안이 명시한 경계 4가지 중 지금 실제로 필요한 세 가지를 구현한다:
1. selector 동치 (Recorder UI 대상)
2. 조건식 동치 (If/Loop 조건)
3. 패키지·액션명 정규화 - 실측으로 새로 발견한 문제: 예측이 내놓은 패키지명이
   canonical하지 않을 수 있다(예: "Microsoft 365 Excel package in Automation 360"
   vs 카탈로그의 진짜 canonical 이름 "Microsoft 365 Excel"). 이건 그냥 LLM에게
   "이게 뭐야?"라고 묻지 않고, 먼저 실제 RAG 카탈로그를 하이브리드 검색+리랭킹해서
   후보를 뽑아준 다음(app.services.rag.search_actions()를 그대로 재사용,
   `/api/rag/debug/search-actions` 디버그 엔드포인트 경유) 그 후보 중에서
   4o-mini가 고르게 한다 - 맨땅에 헤딩시키지 않고 근거 있는 후보를 준다.

나머지(액션 조합 동치, Python/JS/DLL 내부 의미)는 아직 이 파이프라인에 연결할 실제
사용처가 없어서(비교 대상 predicted workflow가 마땅치 않음) 미구현 상태로 남겨둔다.
없는 걸 미리 만들지 않는다."""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from openai import OpenAI

OPENAI_MODEL = "gpt-4o-mini"
BACKEND_BASE_URL = "http://localhost:8000"
ENV_PATH = Path(
    r"C:\Users\KDH\Documents\VisualStudio Code\A360-Assistant\A360-Assistant-Backend-eval-clean\.env"
)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        text = ENV_PATH.read_text(encoding="utf-8")
        m = re.search(r"^OPENAI_API_KEY=(.*)$", text, re.MULTILINE)
        _client = OpenAI(api_key=m.group(1).strip())
    return _client


def _ask(prompt: str) -> dict:
    resp = _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = resp.choices[0].message.content
    m = re.search(r"판정:\s*(동일|다름|불확실)", content)
    verdict = {"동일": "same", "다름": "different", "불확실": "uncertain"}.get(m.group(1), "uncertain") if m else "uncertain"
    reason_m = re.search(r"이유:\s*(.+)", content, re.DOTALL)
    reason = reason_m.group(1).strip() if reason_m else content.strip()
    return {"verdict": verdict, "reason": reason, "raw": content}


def judge_selector_equivalence(gold_target: dict, pred_target: dict) -> dict:
    """두 UI 객체(css_selector/dom_xpath/html_tag/html_class/frame_src)가 같은
    화면 요소를 가리키는지 판정. 정적 문자열 비교로는 안 되는 경우에만 호출한다
    (compare.py에서 css_selector가 다를 때 uncertain으로 표시되는 지점)."""
    prompt = f"""당신은 RPA 워크플로우의 UI 자동화 대상을 비교하는 평가자입니다.

두 UI 객체 정보가 같은 화면 요소(같은 버튼/입력창/테이블 등)를 가리키는지 판정하세요.
selector 문자열이 달라도, 같은 웹사이트의 같은 종류 요소를 가리키면 "동일"로 판정할 수
있습니다. 반대로 문자열이 비슷해도 다른 사이트/다른 요소면 "다름"입니다.

[정답 워크플로우의 대상]
- 웹사이트: {gold_target.get('frame_src')}
- CSS Selector: {gold_target.get('css_selector')}
- DOM XPath: {gold_target.get('dom_xpath')}
- HTML 태그: {gold_target.get('html_tag')}
- HTML class: {gold_target.get('html_class')}
- 화면에 보이는 텍스트(일부): {gold_target.get('inner_text_snippet')}

[에이전트가 예측한 워크플로우의 대상]
- 웹사이트: {pred_target.get('frame_src')}
- CSS Selector: {pred_target.get('css_selector')}
- DOM XPath: {pred_target.get('dom_xpath')}
- HTML 태그: {pred_target.get('html_tag')}
- HTML class: {pred_target.get('html_class')}
- 화면에 보이는 텍스트(일부): {pred_target.get('inner_text_snippet')}

아래 형식으로 답하세요:
판정: (동일/다름/불확실)
이유: (한두 문장)
"""
    return _ask(prompt)


def judge_condition_equivalence(gold_condition: str, pred_condition: str) -> dict:
    """복잡한 조건식 두 개가 실질적으로 같은 판단을 하는지 판정 (예: If 노드의
    조건, Loop의 while 조건 등). 연산자/피연산자가 정확히 같으면 이 함수를 호출할
    필요조차 없다 - 정적 규칙(문자열 완전일치)으로 이미 처리 가능하므로, 다를 때만
    호출한다."""
    prompt = f"""당신은 RPA 워크플로우의 조건식을 비교하는 평가자입니다.

두 조건식이 실질적으로 같은 판단(같은 입력에 대해 같은 참/거짓)을 내리는지
판정하세요. 변수명이 달라도 논리적으로 동등하면 "동일"입니다.

[정답 조건식] {gold_condition}
[에이전트 예측 조건식] {pred_condition}

아래 형식으로 답하세요:
판정: (동일/다름/불확실)
이유: (한두 문장)
"""
    return _ask(prompt)


def judge_action_equivalence(
    gold_label: str,
    gold_common: dict,
    pred_label: str,
    pred_common: dict,
) -> dict:
    """action_matching.py의 Judge Match 단계에서, 유사도 기준 "상호 Top-1"으로
    이미 후보가 1개로 좁혀진 (gold, pred) 액션 쌍만 여기로 넘어온다. 이 액션
    하나가 서로 대체 가능한지만 판정한다 - "같은 상위 업무 목적"이라는 이유만으로
    동일 판정하면 안 된다(여러 액션으로 이뤄진 구현의 일부 vs 단일 액션 비교
    같은 다대일 케이스를 Judge가 몰래 봐주는 걸 막기 위함, 실제로 우려됐던 문제)."""
    prompt = f"""당신은 RPA 워크플로우의 액션 동치 여부를 판정하는 평가자입니다.

다음 두 액션 "하나씩"이 서로 대체 가능한지 판정하세요.

중요한 원칙:
- 두 액션이 같은 상위 업무 목적에 기여하는지가 아니라, **각 액션 하나가
  수행하는 원자적 동작이 서로 대체 가능한지**만 판단하세요.
- 여러 액션으로 이뤄진 구현의 일부와 이 단일 액션을 비교하는 경우, 그 하나
  만으로 대체된다고 확신할 수 없으면 "다름" 또는 "불확실"로 판단하세요.
- 아래 label/rationale/notes 같은 설명 텍스트에 어떤 업무가 언급돼 있어도,
  실제 package.action이 그 업무를 수행하는 게 아니면 구현된 것으로 판단하지
  마세요 (실제 액션과 파라미터가 최우선 증거입니다).

[정답 액션] {gold_label}
- operation: {gold_common.get('operation')}
- target_text: {gold_common.get('target_text')}

[예측 액션] {pred_label}
- operation: {pred_common.get('operation')}
- target_text: {pred_common.get('target_text')}

아래 형식으로 답하세요:
판정: (동일/다름/불확실)
이유: (한두 문장)
"""
    return _ask(prompt)


def search_action_candidates(query: str, k: int = 5) -> list[dict]:
    """실제 production 검색(app.services.rag.search_actions() - 하이브리드 BM25+
    벡터+RRF+리랭킹)을 그대로 호출해 후보를 가져온다. 로컬 eval-clean 백엔드가
    떠 있고 .env에 DEBUG_ENDPOINTS_ENABLED=true가 설정돼 있어야 한다."""
    resp = requests.get(
        f"{BACKEND_BASE_URL}/api/rag/debug/search-actions",
        params={"q": query, "k": k, "source_types": "action_schema"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def normalize_action_label(messy_package: str, messy_action: str) -> dict:
    """예측이 내놓은 package/action 문자열이 카탈로그의 canonical 이름과 다를 때,
    실제 카탈로그를 검색+리랭킹한 후보를 4o-mini에게 주고 어느 (package, action)에
    해당하는지 고르게 한다. 맨땅에 헤딩(카탈로그 근거 없이 추측)시키지 않는다."""
    query = f"{messy_package} {messy_action}"
    candidates = search_action_candidates(query, k=5)
    if not candidates:
        return {"verdict": "no_match", "canonical_package": None, "canonical_action": None, "reason": "카탈로그 검색 결과 없음"}

    candidate_lines = []
    seen = set()
    for c in candidates:
        key = (c.get("package_name"), c.get("action_name"))
        if key in seen:
            continue
        seen.add(key)
        candidate_lines.append(
            f"- package={c.get('package_name')!r}, action={c.get('action_name')!r} "
            f"(리랭크 점수 {c.get('rerank_score')}): {c.get('title')}"
        )

    prompt = f"""당신은 RPA 액션 카탈로그 정규화 평가자입니다.

에이전트가 예측한 워크플로우에 다음 액션이 있었습니다:
package={messy_package!r}, action={messy_action!r}

실제 카탈로그를 검색해서 나온 후보들입니다(관련도 순):
{chr(10).join(candidate_lines)}

이 예측 액션이 후보 중 어느 것과 같은 실제 A360 액션인지 고르세요. 후보 중에 맞는
게 없으면 "없음"이라고 답하세요.

아래 형식으로 답하세요:
package: (고른 후보의 package 값 그대로, 없으면 "없음")
action: (고른 후보의 action 값 그대로, 없으면 "없음")
이유: (한두 문장)
"""
    resp = _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = resp.choices[0].message.content
    pkg_m = re.search(r"package:\s*(.+)", content)
    act_m = re.search(r"action:\s*(.+)", content)
    reason_m = re.search(r"이유:\s*(.+)", content, re.DOTALL)
    pkg = pkg_m.group(1).strip().strip("'\"") if pkg_m else None
    act = act_m.group(1).strip().strip("'\"") if act_m else None
    if pkg in (None, "없음") or act in (None, "없음"):
        return {"verdict": "no_match", "canonical_package": None, "canonical_action": None, "reason": (reason_m.group(1).strip() if reason_m else content), "raw": content, "candidates": candidates}
    return {"verdict": "matched", "canonical_package": pkg, "canonical_action": act, "reason": (reason_m.group(1).strip() if reason_m else ""), "raw": content, "candidates": candidates}
