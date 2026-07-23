"""RAGAS 기본(default) 프롬프트에서 실제로 고치기로 확정한 건 AnswerRelevancy 역질문의
언어 문제 하나뿐이다(2026-07-23 결정).

처음엔 Faithfulness statement 분해의 양태 왜곡(할 수 있다=가능 → 해야 한다=필수로 바뀌는 문제,
폴더 가져오기 세션이름 케이스에서 실측 확인)도 커스텀 프롬프트로 고치려 했으나, EXAONE이 그
지시를 완전히 못 따라서(옛 왜곡 패턴을 없애지 않고 새 문장만 옆에 추가함 — 오히려 statement
개수가 늘어 점수가 더 불안정해짐) 실험 설계상 더 단순하고 공정한 쪽으로 결정을 바꿨다:
**Faithfulness/ContextPrecision/ContextRecall/AnswerCorrectness는 RAGAS 기본 프롬프트 그대로
쓰고, 두 judge(gpt-4o-mini·EXAONE) 모두 같은 결함을 안은 채로 동일 기준으로 비교되므로
상대 비교에는 쓸 수 있다고 본다.** AnswerRelevancy만 언어 문제가 명확하고(원 답변은 한국어인데
역생성 질문이 EXAONE 30/30, gpt-4o-mini 19/30 영어로 나옴 — 커스텀 프롬프트로 언어 문제
자체는 두 모델 다 100% 해결 확인됨) 고쳐도 판단 로직 자체를 바꾸는 게 아니라서 예외로 둔다.

judge 모델(로컬 EXAONE이든 gpt-4o-mini든)과 무관하게 항상 이 커스텀 프롬프트를 쓴다 —
조건마다 다른 프롬프트를 쓰면 그 자체가 비교를 불공정하게 만들기 때문이다.
"""
from ragas.metrics._answer_relevance import (
    ResponseRelevanceInput,
    ResponseRelevanceOutput,
    ResponseRelevancePrompt,
)


class LanguageMatchedResponseRelevancePrompt(ResponseRelevancePrompt):
    """원문 예시(영어 2개)에 한국어 예시 1개를 추가하고, 언어를 답변과 맞추라는 지시만 추가."""

    instruction = (
        ResponseRelevancePrompt.instruction
        + " Always generate the question in the same language as the given answer — "
        "if the answer is written in Korean, the generated question must also be "
        "written in Korean; if the answer is written in English, the question must "
        "be in English."
    )
    examples = ResponseRelevancePrompt.examples + [
        (
            ResponseRelevanceInput(
                response="폴더 가져오기 작업에서 세션 이름은 직접 입력하거나, 세션 이름이 저장된 변수를 선택할 수 있습니다.",
            ),
            ResponseRelevanceOutput(
                question="폴더 가져오기 작업에서 세션 이름은 어떻게 지정할 수 있나요?",
                noncommittal=0,
            ),
        ),
    ]
