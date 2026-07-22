"""120개 unmatched leaf + 69개 미판단 category를 agent로 재처리해서 packages.json에 병합.

기존 actions는 안 건드리고(overwrite 아님), 새로 진짜 액션으로 판정된 것만 append한다.
"""
import json
from app.rag import config
from app.rag.build.merge import load_docs
from app.rag.pipeline import _discover_packages
from app.rag.agents.package_parser import parse_leaf, _leaf_action_name

docs = load_docs(config.DOCS_JSONL)
en_docs = load_docs(config.docs_jsonl_for_locale("en-US"))
discovered = _discover_packages(docs, en_docs)

packages = json.loads(config.PACKAGES_JSON.read_text(encoding="utf-8"))
by_name = {p["package_name"]: p for p in packages}

# 대상 수집: (package_name, node_type, doc_dict)
targets = []
for pkg_name, tree in discovered.items():
    pkg = by_name.get(pkg_name)
    if pkg is None or pkg.get("schema_source") != "llm_agent":
        continue
    existing_names = {a["name"] for a in pkg.get("actions", [])}
    for leaf in tree.leaves:
        if _leaf_action_name(leaf.doc) not in existing_names:
            targets.append((pkg_name, "leaf", leaf.doc))
    for cat in tree.category_docs:
        if _leaf_action_name(cat.doc) not in existing_names:
            targets.append((pkg_name, "category", cat.doc))

print(f"재처리 대상: {len(targets)}개")

added = 0
rejected = 0
failed = 0
new_actions_by_pkg: dict[str, list] = {}
for i, (pkg_name, node_type, doc) in enumerate(targets, 1):
    try:
        action = parse_leaf(pkg_name, doc, model=None)
    except Exception as exc:
        failed += 1
        print(f"  [{i}/{len(targets)}] 실패 [{node_type}] {pkg_name} / {doc.get('title')}: {exc}")
        continue
    if action is None:
        rejected += 1
        print(f"  [{i}/{len(targets)}] 거부(비-액션) [{node_type}] {pkg_name} / {doc.get('title')}")
        continue
    added += 1
    new_actions_by_pkg.setdefault(pkg_name, []).append(action.to_dict())
    print(f"  [{i}/{len(targets)}] 액션 확인 [{node_type}] {pkg_name} / {doc.get('title')} -> {action.name}")

print()
print(f"결과: 신규 액션 {added}개 / 정상 거부 {rejected}개 / 파싱 실패 {failed}개")

# packages.json에 병합 (append, 기존 것 안 건드림, name 중복만 방지)
for pkg_name, new_actions in new_actions_by_pkg.items():
    pkg = by_name[pkg_name]
    existing_names = {a["name"] for a in pkg.get("actions", [])}
    for na in new_actions:
        if na["name"] not in existing_names:
            pkg.setdefault("actions", []).append(na)
            existing_names.add(na["name"])

config.PACKAGES_JSON.write_text(
    json.dumps(list(by_name.values()), ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"저장 완료 → {config.PACKAGES_JSON}")
