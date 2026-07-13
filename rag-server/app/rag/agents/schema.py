"""문서 파싱 에이전트의 구조화 출력 스키마 (pydantic).

에이전트는 JAR이 없는 패키지의 리프 문서(structured_html)에서 액션을 뽑아내는데, 그 출력이
**JAR 파서(sources/jar_parser.py)가 만드는 packages.json 형태와 동일**해야 build_rag_documents가
그대로 action_schema로 변환한다(계약 검증됨). 여기 pydantic 모델은 LLM 출력을 그 형태로
강제·검증하기 위한 것이고, to_action_dict()/to_package_dict()가 최종 packages.json dict로 바꾼다.

신뢰 등급: 에이전트가 뽑은 액션은 JAR과 달리 미검증이라 package dict에 schema_source="llm_agent"를
달아 내보낸다 — merge.py가 이 값을 그대로 action_schema metadata에 실어 JAR("jar")과 구분한다.
"""

from pydantic import BaseModel, Field

SCHEMA_SOURCE = "llm_agent"


class ParsedParameter(BaseModel):
    """액션 파라미터 하나 — JAR 파라미터 dict와 동일 키(name/label/type/required/...)."""

    name: str
    label: str | None = None
    description: str | None = None
    type: str | None = None
    required: bool = False
    default: object | None = None
    options: list[str] | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "label": self.label or self.name,
            "type": self.type,
            "required": self.required,
        }
        if self.description:
            d["description"] = self.description
        if self.default is not None:
            d["default"] = self.default
        if self.options:
            d["options"] = self.options
        return d


class ParsedAction(BaseModel):
    """리프 문서 하나에서 추출한 액션. is_action=False면 진짜 액션이 아니라 참고/예제 문서로 보고 버린다.

    docs에는 JAR의 내부 command name이 노출되지 않는 경우가 많아, name은 에이전트가 제목에서
    추론한 안정적 식별자(예: 제목의 camelCase)다. 실제 Control Room command name과 다를 수 있어
    schema_source=llm_agent로 미검증임을 표시한다.
    """

    is_action: bool = Field(
        default=True,
        description="이 리프가 실제 실행 가능한 액션이면 true, 개념 설명·예제·목차면 false",
    )
    name: str = Field(description="액션의 안정적 식별자(제목 기반 camelCase 등)")
    label: str | None = Field(default=None, description="사람이 읽는 액션 이름(문서 제목)")
    description: str | None = None
    return_type: str | None = None
    return_label: str | None = None
    parameters: list[ParsedParameter] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label or self.name,
            "description": self.description,
            "return_type": self.return_type,
            "return_label": self.return_label,
            "parameters": [p.to_dict() for p in self.parameters],
        }


def build_package_dict(
    package_name: str,
    actions: list[ParsedAction],
    *,
    package_label: str | None = None,
    package_description: str | None = None,
) -> dict:
    """에이전트가 뽑은 액션들을 JAR 파서와 동일한 packages.json 항목 dict로 조립한다.

    package_version은 docs에서 알 수 없어 None. schema_source="llm_agent"로 신뢰 등급을 표시한다.
    """
    return {
        "package_name": package_name,
        "package_label": package_label or package_name,
        "package_description": package_description or "",
        "package_version": None,
        "schema_source": SCHEMA_SOURCE,
        "actions": [a.to_dict() for a in actions],
    }
