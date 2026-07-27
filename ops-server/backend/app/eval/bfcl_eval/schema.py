"""BFCL(Berkeley Function Calling Leaderboard) 방식 액션 호출 평가 스키마.

A360 Agent가 추천하는 액션(package+action+parameters)은 구조적으로 BFCL의
"함수 호출"과 동일하다. 이 모듈은 BFCL 논문(Test/BFCL 논문 정리.md)의 평가방식
표를 그대로 따른다 — 카테고리마다 실제 논문이 쓰는 채점 방식을 적용하고, A360에
구조적으로 대응물이 없는 카테고리(Memory/Web Search/SQL/Execution Response
Matching)는 만들지 않는다:

  Single-Turn(simple/multiple)/Crowd-Sourced → AST Substring Matching
  Multi-Turn(multi_turn_state)                → State-Based Evaluation
  Multi-Turn(response_based)                  → Response-Based Evaluation(단,
    A360은 워크플로우를 실행하지 않고 한 턴에 여러 스텝을 한 번에 생성하므로
    "턴 여러 개"가 아니라 "한 워크플로우 안의 스텝 순서"에 적용한다 — 논문의
    get_zipcode_by_city→get_weather_by_zipcode 선행관계 검사를 A360의 세션형
    액션(OpenSpreadsheet 없이 SetCell을 쓰면 안 됨, checker.py R7)에 대응시킴)
  Missing Parameters/Missing Functions        → Multi-Turn 하위 유형(논문 236~252줄)

AST Substring Matching(논문 463~472줄)의 핵심은 "파라미터 값이 하나의 고정
문자열과 완전히 일치할 필요 없이, 미리 정의된 유효값 '집합'에 속하는가"다.
function name도 마찬가지로 — Multiple 카테고리(여러 도구 후보 중 정확히 골라야
하는 상황)에서는 정답이 하나의 (package, action)이 아니라 "허용 가능한 후보
집합"이어야 한다. 실측으로 확인됨: A360 카탈로그에 `Excel/SetCell`과
`Excel_MS/SetCell`이 파라미터명(setCellType vs cellOption)까지 다른 별개의
정식 액션으로 둘 다 존재하고, 검색 rerank 점수도 0.910/0.914로 사실상 동률이라
실제로 이런 근접 후보 상황이 A360 카탈로그에 있다.

ExpectedTarget이 "허용 가능한 정답 하나"를 표현한다 — simple/missing_parameters는
target 1개, multiple은 target 2개 이상(그중 하나만 맞아도 name_match).
"""

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "simple",
    "multiple",
    "irrelevance",
    "missing_parameters",
    "missing_functions",
    "multi_turn_state",
    "response_based",
]


class ParamCheck(BaseModel):
    """파라미터 하나의 정답 판정 방식 — BFCL의 "값이 유효값 집합에 속하는가" 개념.

    check: exact(주어진 값들 중 하나와 정확히 일치) | enum(선택지 중 하나) |
    contains(부분 문자열 포함, 여러 개면 그중 하나만 포함해도 통과) |
    nonempty(비어있지만 않으면 정답) | bool_true/bool_false(불리언 값).
    exact/contains의 expected는 단일 문자열 또는 문자열 리스트(허용 값 집합) —
    리스트면 그중 하나만 만족해도 통과한다(논문의 "predefined set of valid values").
    """

    name: str
    check: Literal["exact", "enum", "contains", "nonempty", "bool_true", "bool_false"]
    expected: str | float | list[str] | None = None


class ExpectedTarget(BaseModel):
    """허용 가능한 정답 후보 하나 — (package, action) + 그 액션의 파라미터 체크.

    require_no_prereq_violation=True면 Response-Based Evaluation(논문 548~570줄,
    선행 호출을 실제로 거쳤는지 확인)을 겸한다 — 이 target으로 찾은 액션에
    checker.py R7(세션 생명주기) 위반이 없어야 통과. "get_zipcode_by_city 없이
    get_weather_by_zipcode를 부르면 안 된다"는 논문 예시를 A360 세션형 액션
    (Excel_MS 등 OpenSpreadsheet 없이 SetCell)에 대응시킨 것.
    """

    package: str
    action: str
    params: list[ParamCheck] = Field(default_factory=list)
    require_no_prereq_violation: bool = False


class BFCLTurn(BaseModel):
    """대화 턴 하나. Multi-Turn(논문 516~524줄): "모든 턴에서 통과해야 전체가 정답".

    message: 이 턴에 보낼 사용자 메시지. simple/multiple/missing_* 케이스는 턴이
    1개뿐이고 message는 고정 트리거 문구(_RECOMMEND_TRIGGER)를 쓴다(runner.py) —
    실제 업무 설명은 케이스 레벨 question이 문서로 먼저 등록된다. multi_turn_*
    케이스는 turns[0]이 초기 요청, turns[1]이 후속 수정 지시다.

    missing_params_expected_empty: Missing Parameters 카테고리(논문 236~242줄)
    전용 — "critical parameter를 추론할 수 없으면 채우면 안 된다"가 정답이므로,
    액션을 찾았을 때 이 이름의 파라미터가 '비어 있어야' 통과한다(반대로 값을
    지어냈으면 hallucination으로 실패 처리). 액션 자체를 안 만들었어도(steps 비어
    있음) 통과로 본다 — 둘 다 "critical 정보 없이 확정하지 않음"이라는 같은 정답.
    """

    message: str
    expected_targets: list[ExpectedTarget] = Field(default_factory=list)
    expect_no_action: bool = False
    missing_params_expected_empty: list[str] = Field(default_factory=list)


class BFCLCase(BaseModel):
    case_id: str = Field(min_length=1)
    category: Category
    # 문서로 먼저 등록할 업무 설명. irrelevance처럼 "업무"가 아닌 케이스는 None —
    # 문서 없이 순수 채팅으로 보낸다(runner.py 기존 관례, 문서 등록 요구사항 회피).
    document_text: str | None = None
    turns: list[BFCLTurn] = Field(min_length=1)


class BFCLTurnResult(BaseModel):
    message: str
    actual_type: str | None = None
    actual_package: str | None = None
    actual_action: str | None = None
    actual_params: dict = Field(default_factory=dict)
    matched_target_index: int | None = None  # expected_targets 중 몇 번째가 매치됐는지
    name_match: bool = False
    param_results: dict[str, bool] = Field(default_factory=dict)
    prereq_ok: bool | None = None  # require_no_prereq_violation 대상일 때만 값이 참
    ast_match: bool = False
    violations: list[dict] = Field(default_factory=list)


class BFCLCaseResult(BaseModel):
    case_id: str
    category: str
    question: str  # 케이스 대표 질문(document_text 또는 첫 턴 message) — 리포트 표시용
    turns: list[BFCLTurnResult] = Field(default_factory=list)
    name_match: bool = False  # 모든 턴의 name_match AND
    ast_match: bool = False  # 모든 턴의 ast_match AND — 논문 522~524줄 "모든 턴 통과해야 정답"
    error: str | None = None
