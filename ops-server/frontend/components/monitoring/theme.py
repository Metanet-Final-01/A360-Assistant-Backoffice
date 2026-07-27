"""모니터링 대시보드 전용 디자인 토큰 — teal 포인트 1개 + 슬레이트/화이트 뉴트럴 +
상태(초록/앰버/빨강) 3색으로 총 5색 이내를 유지한다. dataviz 스킬의
validate_palette.js로 검증됨(라이트 모드 기준):
  - teal(#0f8fae) 단독 사용 시 PASS
  - 상태 3색(#15803d/#ca8a04/#dc2626) 조합 시 PASS
    (단, 앰버는 대비 WARN이 있어 배지에 텍스트 라벨을 항상 함께 표기해 보완한다)
"""

TEAL = "#0f8fae"
SLATE_900 = "#172026"
SLATE_600 = "#667085"
SLATE_BORDER = "#e4e7ec"

GREEN = "#15803d"
AMBER = "#ca8a04"
RED = "#dc2626"

STATUS_COLORS = {"2xx": GREEN, "4xx": AMBER, "5xx": RED, "other": SLATE_600}
STATUS_ORDER = ["2xx", "4xx", "5xx"]


def status_class(code: int | float | None) -> str:
    if code is None:
        return "other"
    code = int(code)
    if code >= 500:
        return "5xx"
    if code >= 400:
        return "4xx"
    if code >= 200:
        return "2xx"
    return "other"


def status_color(code: int | float | None) -> str:
    return STATUS_COLORS.get(status_class(code), SLATE_600)
