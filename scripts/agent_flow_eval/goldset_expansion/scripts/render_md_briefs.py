"""사람이 직접 검증해서 만든 .md 업무정의서를, 기존 render_task_briefs.py와 같은
시각 양식의 PDF로 변환한다. `confirmed_goldset/briefs/*.md` -> `confirmed_goldset/pdfs/*.pdf`.

기존 render_task_briefs.py는 task_briefs.json(기계적으로 정리된 요약)을 입력으로
받는데, 이 .md 세트는 사람이 직접 골드셋 액션 시퀀스를 보고 단계별로 재구성한
더 상세한 업무정의서다. 백엔드가 .md 업로드를 지원하지 않아(HTTP 400
INVALID_FILE_TYPE, PDF/PPTX/PPT/DOCX만 허용) PDF로 변환해야 runner_v2.py의
실제 프론트 흐름(업로드→파싱→분석→추천)에 태울 수 있다.

파싱 규칙(모든 업무정의서 파일 전수 확인한 실제 포맷 그대로):
    # 업무 정의서
    과제명: <제목>
    ## Task N — <업무 이름>
    작업 순서:
    1. <스텝>
    ...
    사용 프로그램 및 시스템: <시스템1>, <시스템2>

파일명 관례: 출력 PDF 이름은 원본 .md 파일명(확장자 제외)을 그대로 보존한다
(예: 0085_getnextbusinessworkingday.md -> 0085_getnextbusinessworkingday__teammate_md.pdf).
앞의 4자리 후보 ID가 곧 이 케이스의 gold/goldset.json과 매칭하는 유일한 키이므로,
잘라내면 후보-Gold 매칭이 깨진다."""

from __future__ import annotations

import re
from pathlib import Path

from path_utils import goldset_expansion_dir
from render_task_briefs import build_pdf, register_korean_font

SOURCE_DIR = goldset_expansion_dir() / "confirmed_goldset" / "briefs"
OUTPUT_DIR = goldset_expansion_dir() / "confirmed_goldset" / "pdfs"

_TITLE_RE = re.compile(r"^과제명:\s*(.+)$", re.MULTILINE)
_TASK_HEADER_RE = re.compile(r"^##\s*Task\s*\d+\s*[—-]\s*(.+)$")
_STEP_RE = re.compile(r"^\d+\.\s*(.+)$")
_SYSTEMS_RE = re.compile(r"^사용 프로그램 및 시스템:\s*(.+)$")


def parse_md(text: str) -> tuple[str, list[dict]]:
    title_match = _TITLE_RE.search(text)
    if not title_match:
        raise ValueError("과제명 라인을 찾을 수 없습니다")
    title = title_match.group(1).strip()

    tasks: list[dict] = []
    current: dict | None = None
    in_steps = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        task_header = _TASK_HEADER_RE.match(line)
        if task_header:
            if current is not None:
                tasks.append(current)
            current = {"name": task_header.group(1).strip(), "steps": [], "systems": []}
            in_steps = False
            continue

        if current is None:
            continue

        if line == "작업 순서:":
            in_steps = True
            continue

        systems_match = _SYSTEMS_RE.match(line)
        if systems_match:
            in_steps = False
            current["systems"] = [s.strip() for s in systems_match.group(1).split(",") if s.strip()]
            continue

        if in_steps:
            step_match = _STEP_RE.match(line)
            if step_match:
                current["steps"].append(step_match.group(1).strip())

    if current is not None:
        tasks.append(current)

    if not tasks:
        raise ValueError("Task 블록을 하나도 찾지 못했습니다")
    return title, tasks


def main() -> None:
    font_name = register_korean_font()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    created = []
    for md_path in sorted(SOURCE_DIR.glob("*.md")):
        bot_name = md_path.stem
        text = md_path.read_text(encoding="utf-8")
        try:
            title, tasks = parse_md(text)
        except ValueError:
            print(f"SKIP {md_path.name} (업무정의서 형식 아님)")
            continue
        output_path = OUTPUT_DIR / f"{bot_name}__teammate_md.pdf"
        build_pdf(output_path, title, tasks, font_name)
        created.append(str(output_path))
        print(f"OK {md_path.name} -> {output_path.name} ({len(tasks)} tasks)")

    print(f"\n총 {len(created)}개 PDF 생성 완료: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
