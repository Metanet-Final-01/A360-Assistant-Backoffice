"""LLM JSON 출력 공통 하네스 — A360-Assistant-Backend/app/agent/orchestrator/jsonio.py 이식본.

JSON mode 호출 → pydantic 검증 → 1회 교정(repair). 인프라 오류(RuntimeError: 키 미설정·
인증·rate limit)는 core.llm.chat이 던지는 걸 그대로 올린다. 사용량은 core.llm.chat이 purpose로
귀속 기록한다(usage_context 안에서 호출해야 component/system 태깅이 붙는다).
"""

import json
import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core import llm

logger = logging.getLogger(__name__)

_RESPONSE_FORMAT = {"type": "json_object"}

T = TypeVar("T", bound=BaseModel)


def _parse(raw: str, model_cls: type[T]) -> T:
    return model_cls.model_validate(json.loads(raw))


def chat_json(
    messages: list[dict],
    *,
    purpose: str,
    model_cls: type[T],
    model: str | None = None,
) -> T:
    """JSON mode로 LLM을 호출해 model_cls로 검증한다. 위반 시 1회 교정, 재실패면 ValueError."""
    raw = llm.chat(messages, purpose=purpose, model=model, response_format=_RESPONSE_FORMAT)
    try:
        return _parse(raw, model_cls)
    except (json.JSONDecodeError, ValidationError) as first_error:
        logger.warning("%s 첫 출력 파싱 실패, 1회 교정: %s", purpose, first_error)
        repair_messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    f"위 출력이 지정한 JSON 형식을 만족하지 못했습니다. 오류:\n{first_error}\n"
                    "설명 없이, 형식에 맞는 JSON 객체만 다시 출력하세요."
                ),
            },
        ]
        repaired = llm.chat(repair_messages, purpose=purpose, model=model, response_format=_RESPONSE_FORMAT)
        try:
            return _parse(repaired, model_cls)
        except (json.JSONDecodeError, ValidationError) as second_error:
            raise ValueError(f"{purpose} 출력 파싱 실패(교정 후에도): {second_error}") from second_error
