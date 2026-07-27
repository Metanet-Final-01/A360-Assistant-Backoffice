import ast
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
LAYOUT_PATH = FRONTEND_ROOT / "components" / "layout.py"


def _page_header_positional_limit() -> int:
    tree = ast.parse(LAYOUT_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "page_header":
            return len(node.args.posonlyargs) + len(node.args.args)
    raise AssertionError("components/layout.py에 page_header 정의가 없습니다.")


def test_page_header_calls_follow_shared_component_contract() -> None:
    positional_limit = _page_header_positional_limit()
    violations: list[str] = []

    for path in sorted((FRONTEND_ROOT / "views").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "page_header":
                continue
            if len(node.args) > positional_limit:
                violations.append(
                    f"{path.relative_to(FRONTEND_ROOT)}:{node.lineno} "
                    f"({len(node.args)} > {positional_limit})"
                )

    assert not violations, "page_header 인자 계약 위반: " + ", ".join(violations)
