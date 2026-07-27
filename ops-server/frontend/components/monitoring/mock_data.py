"""재현 가능한 목업 요청 로그 생성기.

실제 데이터 구조(started_at/method/path/status_code/duration_ms/워크플로우)는
A360-Assistant-Backend RAG 파이프라인 요청 로그(monitoring_logs._to_dataframe)와
동일하게 맞춘다. 워크플로우 여부는 별도 필드가 아니라 기존 코드와 같은 규칙
(path에 "/turn" 포함 여부)으로 도출한다.

시간은 반드시 UTC로 생성한다 — 서버가 어느 타임존에서 돌든 화면에 찍히는 시각이
달라지지 않게 하기 위함이다(브라우저 로컬시간 변환에 의존하는 hydration류 불일치를
Streamlit에서도 원천 차단).
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from .theme import status_class

WORKFLOW_PATH_MARKER = "/turn"

# (method, path 템플릿, 표본 가중치) — 실제 RAG 파이프라인 라우트 구성을 흉내낸다.
_PATH_TEMPLATES = [
    ("POST", "/api/sessions/{sid}/turn", 0.34),
    ("GET", "/api/sessions/{sid}/messages", 0.16),
    ("POST", "/api/sessions", 0.10),
    ("GET", "/api/sessions/{sid}", 0.10),
    ("POST", "/api/auth/refresh", 0.08),
    ("GET", "/api/documents/search", 0.10),
    ("GET", "/api/documents/{doc_id}", 0.06),
    ("POST", "/api/feedback", 0.04),
    ("GET", "/api/health", 0.02),
]

# 대부분 200이고 일부 4xx/5xx가 섞이도록 한 가중치.
_STATUS_WEIGHTS = [
    (200, 0.80),
    (201, 0.08),
    (400, 0.03),
    (404, 0.02),
    (422, 0.03),
    (500, 0.02),
    (503, 0.02),
]


def generate_mock_logs(n: int = 100, seed: int = 42, now: datetime | None = None) -> pd.DataFrame:
    """seed가 같으면 경로/메서드/상태/응답시간 분포가 항상 동일한 목업 로그 n건을 만든다.
    now는 UTC 기준 앵커 시각(기본은 호출 시점)이며, 여기서부터 과거로 흩뿌린다."""
    rng = random.Random(seed)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    session_ids = [uuid.UUID(int=rng.getrandbits(128), version=4) for _ in range(14)]
    doc_ids = [f"doc_{rng.randint(1000, 9999)}" for _ in range(10)]

    templates = [t[1] for t in _PATH_TEMPLATES]
    weights = [t[2] for t in _PATH_TEMPLATES]
    method_by_template = {t[1]: t[0] for t in _PATH_TEMPLATES}
    status_codes = [s[0] for s in _STATUS_WEIGHTS]
    status_weights = [s[1] for s in _STATUS_WEIGHTS]

    # 뒤로 갈수록(=과거로 갈수록) 누적되는 임의 간격 — 최근 로그가 촘촘하게 몰리는
    # 실제 트래픽 패턴과 비슷하게 30초 버킷 차트에 여러 건이 걸리도록 한다.
    offsets, acc = [], 0.0
    for _ in range(n):
        acc += rng.uniform(4, 45)
        offsets.append(acc)

    rows = []
    for i in range(n):
        template = rng.choices(templates, weights=weights, k=1)[0]
        path = template
        if "{sid}" in path:
            path = path.replace("{sid}", str(rng.choice(session_ids)))
        if "{doc_id}" in path:
            path = path.replace("{doc_id}", rng.choice(doc_ids))

        method = method_by_template[template]
        if method == "GET" and rng.random() < 0.05:
            method = "POST"

        status_code = rng.choices(status_codes, weights=status_weights, k=1)[0]
        cls = status_class(status_code)
        if cls == "5xx":
            duration_ms = round(rng.uniform(1200, 4800))
        elif cls == "4xx":
            duration_ms = round(rng.uniform(15, 140))
        else:
            duration_ms = round(rng.lognormvariate(4.6, 0.5))

        rows.append(
            {
                "started_at": now - timedelta(seconds=offsets[i]),
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "워크플로우": WORKFLOW_PATH_MARKER in path,
            }
        )

    df = pd.DataFrame(rows).sort_values("started_at").reset_index(drop=True)
    return df
