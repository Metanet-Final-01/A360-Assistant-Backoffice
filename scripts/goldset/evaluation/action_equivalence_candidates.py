from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


GOLDSET_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GOLDSET_ROOT.parents[2]
BACKEND_ROOT = REPO_ROOT / "A360-Assistant-Backend"
DEFAULT_REVIEWED_PAIRS = GOLDSET_ROOT / "evaluation" / "action_equivalence_reviewed_pairs.json"
DEFAULT_CONFIRMED_RULES = GOLDSET_ROOT / "evaluation" / "action_equivalence_rules.json"


PACKAGE_ALIASES = {
    "excel_ms": {"excel", "excel_ms", "excel advanced", "excel_advanced", "microsoft 365 excel"},
    "excel": {"excel", "excel_ms", "excel advanced", "excel_advanced", "microsoft 365 excel"},
    "webautomation": {"webautomation", "browser", "recorder"},
    "browser": {"webautomation", "browser", "recorder"},
    "errorhandler": {"errorhandler", "error handler"},
    "logtofile": {"logtofile", "log to file"},
    "mswordpackage": {"mswordpackage", "word", "microsoft word"},
}

ACTION_ALIASES = {
    "openspreadsheet": {"open", "openworkbook", "open spreadsheet", "open excel workbook"},
    "closespreadsheet": {"close", "closeworkbook", "close spreadsheet"},
    "savespreadsheet": {"save", "saveworkbook", "save spreadsheet"},
    "getmultiplecells": {"getmultiplecells", "get multiple cells", "read range", "get cell range"},
    "setcell": {"setcell", "set cell", "write cell"},
    "gotocell": {"gotocell", "go to cell", "select cell"},
    "readexcelrow": {"read row", "readexcelrow", "get row"},
    "startsessionwebautomation": {"open", "openbrowser", "launchwebsite", "start session", "open page"},
    "endsessionwebautomation": {"close", "close browser", "end session"},
    "openpage": {"open", "open page", "navigate"},
    "sendkeys": {"send keys", "type", "set text", "enter text"},
    "clickelement": {"click", "click element"},
    "getvalueelement": {"get value", "get property", "get text"},
    "elementloaded": {"wait", "wait for element", "element exists"},
    "isloaded": {"page loaded", "is loaded", "wait"},
    "logtofile": {"log to file", "write log", "append log"},
}

SEMANTIC_GROUP_KEYWORDS = {
    "open_navigate": {
        "open",
        "launch",
        "website",
        "navigate",
        "startsession",
        "start session",
        "열기",
        "웹사이트",
        "세션 시작",
    },
    "close_end": {"close", "endsession", "end session", "닫기", "종료", "세션 종료"},
    "download": {"download", "downloadfile", "download files", "파일 다운로드", "다운로드"},
    "click": {"click", "clickelement", "클릭"},
    "type_text": {"sendkeys", "send key", "type", "keystroke", "키 입력"},
    "wait_exists": {"loaded", "wait", "exists", "elementloaded", "isloaded", "로드", "기다", "확인"},
    "read_get": {"get", "query", "read", "capture", "extract", "가져오기", "읽기", "추출", "캡처", "조회"},
    "write_set": {"set", "put", "assign", "replace", "add", "create", "save", "설정", "쓰기", "저장", "생성", "추가", "대체"},
    "delete_remove": {"delete", "remove", "삭제", "제거"},
    "send_submit": {"send", "post", "submit", "mail", "sms", "전송", "제출"},
}

CONFLICTING_SEMANTIC_GROUPS = {
    frozenset({"open_navigate", "close_end"}),
    frozenset({"read_get", "write_set"}),
    frozenset({"write_set", "delete_remove"}),
    frozenset({"open_navigate", "delete_remove"}),
    frozenset({"click", "type_text"}),
    frozenset({"download", "send_submit"}),
}


@dataclass
class GoldAction:
    package: str
    action: str
    count: int
    cases: list[str]
    attr_names: Counter[str]
    attr_types: Counter[str]
    return_types: Counter[str]
    hints: list[str]


@dataclass
class CatalogAction:
    package: str
    action: str | None
    source_type: str
    title: str
    content: str
    schema: dict[str, Any]
    metadata: dict[str, Any]


def norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "", (text or "").lower())


def words(text: str | None) -> str:
    return re.sub(r"[_\-./]+", " ", text or "").lower()


def semantic_groups(*parts: str | None) -> set[str]:
    text = words(" ".join(part or "" for part in parts))
    compact = norm(text)
    groups: set[str] = set()
    for group, keywords in SEMANTIC_GROUP_KEYWORDS.items():
        for keyword in keywords:
            keyword_words = words(keyword)
            if keyword_words and keyword_words in text:
                groups.add(group)
                break
            keyword_norm = norm(keyword)
            if keyword_norm and keyword_norm in compact:
                groups.add(group)
                break
    return groups


def semantic_conflict(left: set[str], right: set[str]) -> bool:
    return any(pair <= (left | right) and pair & left and pair & right for pair in CONFLICTING_SEMANTIC_GROUPS)


def value_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for key in ("string", "expression", "number", "file", "boolean"):
            if key in value and value[key] not in ("", None):
                found.append(str(value[key]))
        for item in value.values():
            found.extend(value_strings(item))
        return found
    if isinstance(value, list):
        return [s for item in value for s in value_strings(item)]
    return []


def walk_steps(steps: list[dict[str, Any]] | None):
    for step in steps or []:
        yield step
        yield from walk_steps(step.get("steps"))
        for branch in step.get("branches") or []:
            yield from walk_steps(branch.get("steps"))


def load_gold_actions(root: Path) -> dict[tuple[str, str], GoldAction]:
    grouped: dict[tuple[str, str], GoldAction] = {}
    for path in sorted((root / "eval_inputs" / "normalized_workflows_13").glob("*/*.goldset.json")):
        case_id = path.parent.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        for step in walk_steps(payload.get("steps")):
            if step.get("type") not in {"action", "container"}:
                continue
            package = step.get("package")
            action = step.get("action")
            if not package or not action:
                continue
            key = (package, action)
            item = grouped.get(key)
            if item is None:
                item = GoldAction(package, action, 0, [], Counter(), Counter(), Counter(), [])
                grouped[key] = item
            item.count += 1
            if case_id not in item.cases:
                item.cases.append(case_id)
            for attr in step.get("attributes") or []:
                if attr.get("name"):
                    item.attr_names[attr["name"]] += 1
                value = attr.get("value")
                if isinstance(value, dict) and value.get("type"):
                    item.attr_types[value["type"]] += 1
                for hint in value_strings(value):
                    if len(hint) > 2 and hint not in item.hints and len(item.hints) < 24:
                        item.hints.append(hint)
            return_to = step.get("return_to") or {}
            if return_to.get("type"):
                item.return_types[return_to["type"]] += 1
    return grouped


def split_action_label(label: str | None) -> tuple[str, str | None]:
    if not label:
        return "", None
    if "." not in label:
        return label, None
    package, action = label.split(".", 1)
    return package, action


def row_action_pair(row: dict[str, Any]) -> tuple[str, str, str, str | None]:
    return (
        row["gold_package"],
        row["gold_action"],
        row["candidate_package"],
        row["candidate_action"],
    )


def load_reviewed_pairs(path: Path) -> set[tuple[str, str, str, str | None]]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    pairs: set[tuple[str, str, str, str | None]] = set()
    for item in payload.get("reviewed_pairs", []) or []:
        gold_package, gold_action = split_action_label(item.get("gold_action"))
        stored_package, stored_action = split_action_label(item.get("stored_action"))
        if gold_package and gold_action and stored_package and stored_action:
            pairs.add((gold_package, gold_action, stored_package, stored_action))
    return pairs


def load_confirmed_equivalence_pairs(path: Path) -> set[tuple[str, str, str, str | None]]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    pairs: set[tuple[str, str, str, str | None]] = set()
    for group in payload.get("equivalence_groups", []) or []:
        members = [split_action_label(member) for member in group.get("members", []) or []]
        members = [(package, action) for package, action in members if package and action]
        for gold_package, gold_action in members:
            for stored_package, stored_action in members:
                if (gold_package, gold_action) != (stored_package, stored_action):
                    pairs.add((gold_package, gold_action, stored_package, stored_action))
    return pairs


def load_excluded_pairs(args: argparse.Namespace) -> set[tuple[str, str, str, str | None]]:
    excluded: set[tuple[str, str, str, str | None]] = set()
    if args.exclude_reviewed and DEFAULT_REVIEWED_PAIRS.exists():
        excluded |= load_reviewed_pairs(DEFAULT_REVIEWED_PAIRS)
    for path in args.exclude_pairs_file:
        excluded |= load_reviewed_pairs(path)
    if args.exclude_confirmed_rules and DEFAULT_CONFIRMED_RULES.exists():
        excluded |= load_confirmed_equivalence_pairs(DEFAULT_CONFIRMED_RULES)
    return excluded


def connect_db():
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.rag.store import db

    return db.connect()


def load_catalog_actions() -> list[CatalogAction]:
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_name, action_name, source_type, title, content, metadata
                FROM rag_documents
                WHERE package_name IS NOT NULL
                  AND source_type IN ('action_schema', 'action_candidate')
                ORDER BY package_name, action_name NULLS LAST, title
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    best: dict[tuple[str, str | None, str, str], CatalogAction] = {}
    for package, action, source_type, title, content, metadata in rows:
        schema = metadata.get("schema") if isinstance(metadata, dict) else None
        key = (package, action, source_type, title if source_type == "action_candidate" else "")
        candidate = CatalogAction(package, action, source_type, title or "", content or "", schema or {}, metadata or {})
        existing = best.get(key)
        if existing is None or len(candidate.content) > len(existing.content):
            best[key] = candidate
    return list(best.values())


def schema_param_tokens(schema: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for param in schema.get("parameters") or []:
        for key in ("name", "label", "type"):
            if param.get(key):
                tokens.add(norm(str(param[key])))
    if schema.get("return_type"):
        tokens.add("return:" + norm(str(schema["return_type"])))
    if schema.get("return_label"):
        tokens.add("returnlabel:" + norm(str(schema["return_label"])))
    return {token for token in tokens if token}


def gold_param_tokens(gold: GoldAction) -> set[str]:
    tokens = {norm(name) for name in gold.attr_names}
    tokens |= {"type:" + norm(t) for t in gold.attr_types}
    tokens |= {"return:" + norm(t) for t in gold.return_types}
    return {token for token in tokens if token}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def name_similarity(left: str, right: str | None) -> float:
    if not right:
        return 0.0
    left_norm = norm(left)
    right_norm = norm(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def package_relation(gold_package: str, candidate_package: str) -> str:
    gp = words(gold_package)
    cp = words(candidate_package)
    if gp == cp:
        return "same_package"
    if norm(gold_package) == norm(candidate_package):
        return "same_package_normalized"
    for aliases in PACKAGE_ALIASES.values():
        if gp in aliases and cp in aliases:
            return "known_package_family"
    return "different_package"


def related_package(gold_package: str, candidate_package: str) -> bool:
    return package_relation(gold_package, candidate_package) != "different_package"


def alias_action_match(gold_action: str, candidate_action: str | None) -> bool:
    if not candidate_action:
        return False
    ga = norm(gold_action)
    ca_words = words(candidate_action)
    ca_norm = norm(candidate_action)
    if ga == ca_norm:
        return True
    aliases = ACTION_ALIASES.get(ga, set())
    return ca_words in aliases or ca_norm in {norm(a) for a in aliases}


def llm_judgement(
    gold: GoldAction,
    candidate: CatalogAction,
    text_score: float,
    param_score: float,
    action_name_score: float,
) -> tuple[str, str]:
    relation = package_relation(gold.package, candidate.package)
    alias_match = alias_action_match(gold.action, candidate.action)
    gold_groups = semantic_groups(gold.package, gold.action)
    candidate_groups = semantic_groups(candidate.package, candidate.action, candidate.title)
    shared_groups = gold_groups & candidate_groups
    if semantic_conflict(gold_groups, candidate_groups) and not alias_match:
        return "weak", "패키지군은 비슷해도 액션의 핵심 동사가 달라 치환 후보로 낮게 봅니다."
    if relation.startswith("same_package") and (action_name_score >= 0.92 or alias_match):
        return "strong", "동일 패키지에서 액션명이 같거나 거의 같아 우선 검증 후보입니다."
    if relation == "known_package_family" and (alias_match or shared_groups or action_name_score >= 0.55):
        return "strong", "legacy/current로 의심되는 패키지군이며 액션 의미가 유사합니다."
    if relation.startswith("same_package") and (shared_groups or text_score >= 0.18 or param_score >= 0.2):
        return "possible", "같은 패키지 안에서 원문 또는 파라미터 구조가 유사합니다."
    if relation == "known_package_family" and shared_groups and (text_score >= 0.08 or param_score >= 0.1):
        return "possible", "관련 패키지군 안에서 액션 의미와 일부 단서가 겹칩니다."
    if shared_groups and text_score >= 0.28 and param_score >= 0.2:
        return "possible", "패키지는 다르지만 원문과 파라미터 구조가 함께 유사합니다."
    return "weak", "자동 점수는 남기지만 우선순위가 낮은 후보입니다."


def gold_text(gold: GoldAction) -> str:
    return " ".join(
        [
            gold.package,
            gold.action,
            " ".join(gold.attr_names),
            " ".join(gold.attr_types),
            " ".join(gold.return_types),
            " ".join(gold.hints),
        ]
    )


def catalog_text(action: CatalogAction) -> str:
    schema = action.schema or {}
    params = " ".join(
        " ".join(str(param.get(k, "")) for k in ("name", "label", "description", "type"))
        for param in schema.get("parameters") or []
    )
    return " ".join(
        [
            action.package,
            action.action or "",
            action.title,
            schema.get("label") or "",
            schema.get("description") or "",
            schema.get("return_type") or "",
            schema.get("return_label") or "",
            params,
            action.content[:2000],
        ]
    )


def build_candidates(gold_actions: dict[tuple[str, str], GoldAction], catalog: list[CatalogAction], top_k: int) -> list[dict[str, Any]]:
    gold_items = list(gold_actions.values())
    gold_docs = [gold_text(item) for item in gold_items]
    catalog_docs = [catalog_text(item) for item in catalog]
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
    matrix = vectorizer.fit_transform(gold_docs + catalog_docs)
    gold_matrix = matrix[: len(gold_docs)]
    catalog_matrix = matrix[len(gold_docs) :]
    sims = cosine_similarity(gold_matrix, catalog_matrix)

    rows: list[dict[str, Any]] = []
    for gold_index, gold in enumerate(gold_items):
        gold_tokens = gold_param_tokens(gold)
        ranked = set(int(i) for i in np.argsort(-sims[gold_index])[: max(top_k * 5, top_k)])
        # Text similarity alone tends to keep legacy actions inside the same old package.
        # For human review of old/new equivalence, always include known package-family
        # neighbors even when their wording is sparse or only available as action_candidate.
        for catalog_index, candidate in enumerate(catalog):
            if related_package(gold.package, candidate.package):
                ranked.add(catalog_index)
        scored: list[dict[str, Any]] = []
        for catalog_index in ranked:
            candidate = catalog[int(catalog_index)]
            text_score = float(sims[gold_index, catalog_index])
            param_score = jaccard(gold_tokens, schema_param_tokens(candidate.schema))
            action_score = name_similarity(gold.action, candidate.action)
            pkg_relation = package_relation(gold.package, candidate.package)
            judgement, reason = llm_judgement(gold, candidate, text_score, param_score, action_score)
            combined = (text_score * 0.45) + (param_score * 0.25) + (action_score * 0.20)
            if pkg_relation != "different_package":
                combined += 0.10
            if judgement == "strong":
                combined += 0.10
            scored.append(
                {
                    "gold_package": gold.package,
                    "gold_action": gold.action,
                    "gold_count": gold.count,
                    "gold_cases": ";".join(sorted(gold.cases)),
                    "candidate_package": candidate.package,
                    "candidate_action": candidate.action,
                    "candidate_title": candidate.title,
                    "candidate_source_type": candidate.source_type,
                    "package_relation": pkg_relation,
                    "text_similarity": round(text_score, 4),
                    "param_similarity": round(param_score, 4),
                    "name_similarity": round(action_score, 4),
                    "combined_score": round(combined, 4),
                    "llm_judgement": judgement,
                    "llm_reason": reason,
                    "candidate_scope": candidate.metadata.get("candidate_scope"),
                    "candidate_has_children": candidate.metadata.get("has_children"),
                    "gold_observed_params": sorted(gold_tokens)[:60],
                    "candidate_schema_params": sorted(schema_param_tokens(candidate.schema))[:60],
                }
            )
        scored.sort(
            key=lambda row: (
                row["package_relation"] == "different_package",
                row["llm_judgement"] != "strong",
                row["package_relation"].startswith("same_package"),
                -row["combined_score"],
                -row["text_similarity"],
            )
        )
        rows.extend(scored[:top_k])
    return rows


def write_outputs(rows: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "action_equivalence_candidates.jsonl"
    json_path = output_dir / "action_equivalence_candidates.json"
    csv_path = output_dir / "action_equivalence_candidates.csv"
    summary_path = output_dir / "summary.md"

    jsonl_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "gold_package",
        "gold_action",
        "gold_count",
        "gold_cases",
        "candidate_package",
        "candidate_action",
        "candidate_title",
        "candidate_source_type",
        "package_relation",
        "text_similarity",
        "param_similarity",
        "name_similarity",
        "combined_score",
        "llm_judgement",
        "llm_reason",
        "candidate_scope",
        "candidate_has_children",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})

    by_judgement = Counter(row["llm_judgement"] for row in rows)
    unique_gold = {(row["gold_package"], row["gold_action"]) for row in rows}
    strong_gold = {(row["gold_package"], row["gold_action"]) for row in rows if row["llm_judgement"] == "strong"}
    lines = [
        "# Action Equivalence Candidate Summary",
        "",
        f"- Gold actions covered: `{len(unique_gold)}`",
        f"- Candidate rows: `{len(rows)}`",
        f"- Strong candidate rows: `{by_judgement.get('strong', 0)}`",
        f"- Gold actions with at least one strong candidate: `{len(strong_gold)}`",
        f"- Possible candidate rows: `{by_judgement.get('possible', 0)}`",
        f"- Weak candidate rows: `{by_judgement.get('weak', 0)}`",
        "",
        "Outputs:",
        f"- `{jsonl_path.name}`",
        f"- `{json_path.name}`",
        f"- `{csv_path.name}`",
        "",
        "Columns `text_similarity` and `param_similarity` are machine scores. `llm_judgement` is a heuristic LLM-style prior for human review, not an approved mapping.",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_review_outputs(rows, output_dir)


def review_row(row: dict[str, Any]) -> dict[str, Any]:
    gold_action = f"{row['gold_package']}.{row['gold_action']}"
    stored_action = f"{row['candidate_package']}.{row['candidate_action'] or ''}".rstrip(".")
    return {
        "gold_action": gold_action,
        "gold_count": row["gold_count"],
        "gold_cases": row["gold_cases"],
        "stored_action": stored_action,
        "stored_title": row["candidate_title"],
        "stored_source_type": row["candidate_source_type"],
        "llm_judgement": row["llm_judgement"],
        "llm_reason": row["llm_reason"],
        "is_exact_stored_match": gold_action == stored_action,
        "package_relation": row["package_relation"],
        "text_similarity": row["text_similarity"],
        "param_similarity": row["param_similarity"],
        "name_similarity": row["name_similarity"],
        "combined_score": row["combined_score"],
        "candidate_scope": row["candidate_scope"],
        "candidate_has_children": row["candidate_has_children"],
    }


def review_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    judgement_rank = {"strong": 0, "possible": 1, "weak": 2}
    relation_rank = {
        "same_package": 0,
        "same_package_normalized": 1,
        "known_package_family": 2,
        "different_package": 3,
    }
    exact_match = row["gold_package"] == row["candidate_package"] and row["gold_action"] == (row["candidate_action"] or "")
    return (
        judgement_rank.get(row["llm_judgement"], 9),
        relation_rank.get(row["package_relation"], 9),
        not exact_match,
        -row["combined_score"],
        -row["text_similarity"],
    )


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "gold_action",
        "gold_count",
        "gold_cases",
        "stored_action",
        "stored_title",
        "stored_source_type",
        "llm_judgement",
        "llm_reason",
        "is_exact_stored_match",
        "package_relation",
        "text_similarity",
        "param_similarity",
        "name_similarity",
        "combined_score",
        "candidate_scope",
        "candidate_has_children",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_review_outputs(rows: list[dict[str, Any]], output_dir: Path) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["gold_package"], row["gold_action"])].append(row)

    review_rows: list[dict[str, Any]] = []
    shortlist_rows: list[dict[str, Any]] = []
    for key in sorted(grouped):
        candidates = sorted(grouped[key], key=review_sort_key)
        named_candidates = [row for row in candidates if row["candidate_action"]]
        review_candidates = named_candidates or candidates
        review_rows.extend(review_row(row) for row in review_candidates[:5])

        useful = [row for row in review_candidates if row["llm_judgement"] in {"strong", "possible"}]
        selected = useful[:3] if useful else review_candidates[:1]
        shortlist_rows.extend(review_row(row) for row in selected)

    review_json = output_dir / "action_equivalence_review_gold_to_db.json"
    review_csv = output_dir / "action_equivalence_review_gold_to_db.csv"
    review_non_exact_json = output_dir / "action_equivalence_review_gold_to_db_non_exact.json"
    review_non_exact_csv = output_dir / "action_equivalence_review_gold_to_db_non_exact.csv"
    shortlist_json = output_dir / "action_equivalence_review_gold_to_db_shortlist.json"
    shortlist_csv = output_dir / "action_equivalence_review_gold_to_db_shortlist.csv"
    shortlist_non_exact_json = output_dir / "action_equivalence_review_gold_to_db_shortlist_non_exact.json"
    shortlist_non_exact_csv = output_dir / "action_equivalence_review_gold_to_db_shortlist_non_exact.csv"
    exact_json = output_dir / "action_equivalence_exact_stored_matches.json"
    exact_csv = output_dir / "action_equivalence_exact_stored_matches.csv"

    review_non_exact_rows = [row for row in review_rows if not row["is_exact_stored_match"]]
    shortlist_non_exact_rows = [row for row in shortlist_rows if not row["is_exact_stored_match"]]
    exact_rows = [row for row in review_rows if row["is_exact_stored_match"]]

    review_json.write_text(json.dumps(review_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    shortlist_json.write_text(json.dumps(shortlist_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    review_non_exact_json.write_text(json.dumps(review_non_exact_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    shortlist_non_exact_json.write_text(json.dumps(shortlist_non_exact_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    exact_json.write_text(json.dumps(exact_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review_csv(review_csv, review_rows)
    write_review_csv(shortlist_csv, shortlist_rows)
    write_review_csv(review_non_exact_csv, review_non_exact_rows)
    write_review_csv(shortlist_non_exact_csv, shortlist_non_exact_rows)
    write_review_csv(exact_csv, exact_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build human-review candidates for legacy/current action equivalence.")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, default=GOLDSET_ROOT / "evaluation" / "action_equivalence_candidates")
    parser.add_argument("--exclude-pairs-file", type=Path, action="append", default=[])
    parser.add_argument("--exclude-reviewed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exclude-confirmed-rules", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gold_actions = load_gold_actions(GOLDSET_ROOT)
    catalog = load_catalog_actions()
    rows = build_candidates(gold_actions, catalog, top_k=args.top_k)
    excluded_pairs = load_excluded_pairs(args)
    if excluded_pairs:
        rows = [row for row in rows if row_action_pair(row) not in excluded_pairs]
    write_outputs(rows, args.output_dir)
    print(
        json.dumps(
            {
                "gold_actions": len(gold_actions),
                "catalog_actions": len(catalog),
                "excluded_pairs": len(excluded_pairs),
                "candidate_rows": len(rows),
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
