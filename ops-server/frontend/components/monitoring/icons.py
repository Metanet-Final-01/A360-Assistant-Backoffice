"""KPI 카드·버튼 등 직접 HTML로 그리는 곳에 쓰는 얇은 선(stroke) 아이콘 — Lucide 스타일의
간단한 path만 가져와 씀(사이드바 메뉴 아이콘은 Streamlit 내장 :material: 아이콘을 그대로 쓴다)."""


def _svg(paths: str, size: int = 18, color: str = "currentColor", stroke_width: float = 2) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )


def icon_activity(size: int = 18, color: str = "currentColor") -> str:
    return _svg('<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>', size, color)


def icon_alert_triangle(size: int = 18, color: str = "currentColor") -> str:
    return _svg(
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>'
        '<line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/>',
        size,
        color,
    )


def icon_timer(size: int = 18, color: str = "currentColor") -> str:
    return _svg(
        '<line x1="10" x2="14" y1="2" y2="2"/><line x1="12" x2="15" y1="14" y2="11"/>'
        '<circle cx="12" cy="14" r="8"/>',
        size,
        color,
    )


def icon_share(size: int = 18, color: str = "currentColor") -> str:
    return _svg(
        '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>'
        '<line x1="8.59" x2="15.42" y1="10.51" y2="6.49"/><line x1="8.59" x2="15.42" y1="13.49" y2="17.51"/>',
        size,
        color,
    )


def icon_refresh(size: int = 16, color: str = "currentColor") -> str:
    return _svg(
        '<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M8 16H3v5"/>',
        size,
        color,
    )


def icon_radio(size: int = 16, color: str = "currentColor") -> str:
    return _svg(
        '<path d="M4.93 19.07a10 10 0 0 1 0-14.14"/><path d="M7.76 16.24a6 6 0 0 1 0-8.49"/>'
        '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49"/>'
        '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
        size,
        color,
    )


def icon_pause(size: int = 16, color: str = "currentColor") -> str:
    return _svg('<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>', size, color)


def icon_play(size: int = 16, color: str = "currentColor") -> str:
    return _svg('<polygon points="6 3 20 12 6 21 6 3"/>', size, color)
