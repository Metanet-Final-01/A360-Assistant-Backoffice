from __future__ import annotations

import argparse
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Plain-text stand-in for the reference task-definition PDF: no screenshots, same
# business-facing intent. This is the input fed to the agent under test, not the
# goldset itself, so it should describe the work without exposing exact action names.

_FONT_CANDIDATES = [
    ("Malgun Gothic", r"C:\Windows\Fonts\malgun.ttf"),
    ("Malgun Gothic", r"C:\Windows\Fonts\malgunsl.ttf"),
    ("NanumGothic", r"C:\Windows\Fonts\NanumGothic.ttf"),
]


def register_korean_font() -> str:
    for name, path in _FONT_CANDIDATES:
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont(name, path))
            return name
    raise SystemExit(
        "No Korean-capable TTF font found (checked Malgun Gothic / NanumGothic). "
        "Install one or add its path to _FONT_CANDIDATES."
    )


def build_pdf(output_path: Path, title: str, tasks: list[dict], font_name: str) -> None:
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )
    heading_style = ParagraphStyle("Heading", fontName=font_name, fontSize=18, leading=22, spaceAfter=4)
    label_style = ParagraphStyle("Label", fontName=font_name, fontSize=11, leading=15)
    task_header_style = ParagraphStyle(
        "TaskHeader", fontName=font_name, fontSize=11, leading=15, textColor=colors.white
    )
    body_style = ParagraphStyle("Body", fontName=font_name, fontSize=9.5, leading=13)

    story = [
        Paragraph("업무 정의서", heading_style),
        Spacer(1, 6),
        Table(
            [[Paragraph("과제명", label_style), Paragraph(title, label_style)]],
            colWidths=[30 * mm, 230 * mm],
            style=TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
                    ("INNERGRID", (0, 0), (-1, -1), 0.75, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        ),
        Spacer(1, 10),
    ]

    for index, task in enumerate(tasks, start=1):
        steps_html = "<br/>".join(f"{i}. {step}" for i, step in enumerate(task["steps"], start=1))
        systems = task.get("systems") or []
        systems_html = "<br/>".join(f"- {system}" for system in systems) if systems else "-"
        header = Paragraph(f"Task {index}. {task['name']}", task_header_style)
        body = Paragraph(
            f"<b>{task['name']}</b><br/><br/>"
            f"<font color='#1F3864'>* 작업 순서</font><br/>{steps_html}<br/><br/>"
            f"<font color='#1F3864'>* 사용 프로그램 및 시스템</font><br/>{systems_html}",
            body_style,
        )
        table = Table([[header], [body]], colWidths=[260 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
                    ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
                    ("INNERGRID", (0, 0), (-1, -1), 0.75, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 1), (-1, 1), 6),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 6))
    doc.build(story)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one plain-text task-definition PDF per task brief.")
    parser.add_argument("--briefs", type=Path, default=Path(__file__).resolve().parent / "task_briefs.json")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "pdfs")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    font_name = register_korean_font()
    payload = json.loads(args.briefs.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    created = []
    for candidate in payload["candidates"]:
        source_files = candidate.get("source_files") or [candidate["source_file"]]
        source_stem = "+".join(path.removesuffix(".goldset.json") for path in source_files)
        output_path = args.output_dir / f"{candidate['bot_name']}__{source_stem}.pdf"
        build_pdf(output_path, candidate["title"], candidate["tasks"], font_name)
        created.append(str(output_path))

    print(json.dumps({"created": len(created), "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
