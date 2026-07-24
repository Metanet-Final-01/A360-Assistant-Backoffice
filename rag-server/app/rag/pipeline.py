"""수집 파이프라인 CLI (A360-Assistant-Backend의 RAG 적재 파이프라인 이식본).

여기는 "적재"만 담당한다 — 검색/서빙(hybrid_search, rerank)은 옮기지 않았다(그건
A360-Assistant-Backend가 실시간 에이전트 추천에 계속 쓰는 코드라 그대로 남아있음).
DB(pgvector/OpenSearch)는 백엔드와 동일한 인스턴스를 공유한다 — 여기서 적재한 게
바로 실제 서비스에 반영된다.

파이프라인은 khub 웹크롤 정본 v2 하나뿐이다(팀 결정: 웹크롤 전용). 과거 JAR/GitHub 기반
레거시 명령(crawl v1·build·parse-jars·bots·export-* 등)은 제거됐다. 순서대로 실행한다:

사용 예:
  python -m app.rag.pipeline crawl-khub --dump-dir <dump>                    # ① khub 원문 덤프(toc+bodies[html])
  python -m app.rag.pipeline registry  --dump-dir <dump>                     # ② 패키지 등기부(트리 우선 서브트리 해석)
  python -m app.rag.pipeline build-v2  --dump-dir <dump> --llm-tables --judge --enrich  # ③ 등기부+덤프 → rag_documents.jsonl
  python -m app.rag.pipeline validate  --dump-dir <dump>                     # ④ 품질 게이트(위반 시 종료 1로 적재 차단)
  python -m app.rag.pipeline ingest [--clean]                               # ⑤ 임베딩 → pgvector + OpenSearch 적재

  # 전체 오케스트레이션은 app/rag/scripts/run_option4_full_v2.py (POST /rag/ingest가 이걸 실행).
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import config



def cmd_crawl_khub(args: argparse.Namespace) -> None:
    """khub v2 덤프 생성(toc_*.json + bodies_*.jsonl[html], 주 맵 전수 + 보조 맵) — v2 [크롤] 단계.

    기존 crawl(v1 docs.jsonl)과 달리 registry/build-v2가 소비하는 원문 덤프를 만든다.
    이미 html이 있는 content_id는 건너뛴다(이어받기). run_option4_full_v2가 첫 단계로 부른다.
    """
    from .sources.khub_dump import crawl_khub_dump

    locales = [s.strip() for s in args.locales.split(",") if s.strip()]

    def _prog(map_title: str, i: int, total: int, ok: int, fail: int) -> None:
        print(f"  [{map_title}] {i}/{total} (성공 {ok} / 실패 {fail})", flush=True)

    print(f"[crawl-khub] {', '.join(locales)} → {args.dump_dir}"
          + (f" (limit {args.limit})" if args.limit else ""), flush=True)
    stats = crawl_khub_dump(args.dump_dir, locales=locales, delay=args.delay,
                            limit=args.limit, on_progress=_prog)
    print("[crawl-khub] 완료:", json.dumps(stats, ensure_ascii=False))
    for locale, s in stats.items():
        if s["failed"]:
            print(f"  ⚠️ {locale}: 실패 {s['failed']}건 (재실행하면 이어받는다)")


def cmd_registry(args: argparse.Namespace) -> None:
    """khub 덤프에서 패키지 등기부(package_registry.json) 생성 — v2 파이프라인 [A·B] 단계.

    전략: final-etc-files/회의록/2026-07-18-khub-실측-저장전략.md §2.2 (3소스 합집합).
    """
    from .build.registry import build_registry

    result = build_registry(args.dump_dir)
    out = config.DATA_DIR / "package_registry.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    rep = result["report"]
    print(f"등기부 저장 → {out}")
    print(f"  패키지 총 {rep['total']}개 (문서 서브트리 보유 {rep['with_doc_pages']}개)")
    print(f"  릴리스노트에만 있고 문서 없음({len(rep['release_only(no_doc_pages)'])}): "
          f"{', '.join(rep['release_only(no_doc_pages)'])}")
    print(f"  문서에만 있고 릴리스노트 없음({len(rep['doc_only(not_in_release_notes)'])}): "
          f"{', '.join(rep['doc_only(not_in_release_notes)'])}")


def cmd_validate(args: argparse.Namespace) -> None:
    """빌드 산출물 품질 게이트 — 위반이 있으면 **종료 코드 1**로 적재를 막는다.

    왜 필요한가(실측): `IQ Bot - Document Automation Bridge`는 매 빌드마다 `release_only`
    목록에 **이름째 출력**됐다. 못 본 게 아니라, 진짜로 문서가 없는 8건과 나란히 놓여
    구분할 수 없었고 아무 조치도 강제되지 않았다. 그래서 여기서는 (a) 항목마다 판정 근거를
    붙이고 (b) 결과를 종료 코드로 낸다 — 새 정보를 만드는 게 아니라 이미 있는 신호에
    판정을 붙이는 단계다.

    run_steps가 "한 단계라도 실패하면 멈춘다"이므로, 이 단계가 실패하면 ingest에 도달하지 않는다.
    """
    from .build.registry import walk_toc

    registry_path = config.DATA_DIR / "package_registry.json"
    if not registry_path.exists() or not config.RAG_DOCUMENTS_JSONL.exists():
        sys.exit("package_registry.json / rag_documents.jsonl이 없습니다. registry·build-v2를 먼저 실행하세요.")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    docs = [json.loads(line) for line in open(config.RAG_DOCUMENTS_JSONL, encoding="utf-8")]

    dump = Path(args.dump_dir)
    # 게이트 단계는 traceback이 아니라 명확한 실패 메시지 + 종료코드로 끝나야 한다 — 덤프
    # 오입력/부분 생성/스키마 변경 시 toc를 그대로 읽으면 FileNotFoundError·JSONDecodeError·
    # KeyError로 죽어 운영 디버깅이 어렵다(Qodo 리뷰). 존재·파싱을 먼저 검증한다.
    toc_path = dump / "toc_en-US.json"
    if not toc_path.exists():
        sys.exit(f"{toc_path}이 없습니다 — crawl-khub를 먼저 실행하거나 --dump-dir을 확인하세요.")
    try:
        toc = json.loads(toc_path.read_text(encoding="utf-8"))["toc"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        sys.exit(f"{toc_path} 읽기/파싱 실패({type(exc).__name__}): {exc} — 덤프가 온전한지 확인하세요.")
    flat = walk_toc(toc)
    bodies_have = set()
    for locale in ("en-US", "ko-KR"):
        fp = dump / f"bodies_{locale}.jsonl"
        if not fp.exists():
            continue
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("html"):
                    bodies_have.add(d["content_id"])

    by_cid = {e["content_id"]: e for e in flat if e["content_id"]}
    by_type: dict[str, int] = {}
    for d in docs:
        by_type[d["source_type"]] = by_type.get(d["source_type"], 0) + 1
    acts_by_pkg: dict[str, int] = {}
    for d in docs:
        if d["source_type"] in ("action_schema", "trigger_schema") and d.get("package_name"):
            acts_by_pkg[d["package_name"]] = acts_by_pkg.get(d["package_name"], 0) + 1

    # 빌드가 남긴 통계 — "액션 0건"이 판별로 설명되는지 판단하는 근거로 쓴다.
    stats_path = config.DATA_DIR / "build_stats.json"
    build_stats = {}
    if stats_path.exists():
        try:
            build_stats = json.loads(stats_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            build_stats = {}
    dropped_titles = list((build_stats.get("judge") or {}).get("dropped_titles") or [])
    dropped_titles += list(build_stats.get("skipped_non_action_titles") or [])
    # skipped_non_action_titles는 "Pkg/제목 [hard]" 꼴이라 접미 표시를 떼어 형식을 맞춘다
    dropped_titles = [re.sub(r"\s*\[(hard|soft)\]$", "", t) for t in dropped_titles]

    failures: list[str] = []
    print("=" * 78)
    print("빌드 산출물 검증")
    print("=" * 78)
    if not build_stats:
        print("⚠️ build_stats.json 없음 — 판별 근거 없이 검사합니다(build-v2를 다시 실행하세요)")
    print(f"청크 {len(docs)}개 / source_type: " + ", ".join(f"{k} {v}" for k, v in sorted(by_type.items())))

    # C1 — 등기된 패키지의 서브트리 본문 확보율
    print("\n[C1] 등기 패키지 서브트리 본문 확보")
    gaps = []
    for pkg in registry["packages"]:
        sr = pkg.get("subtree_root")
        if not sr:
            continue
        root = by_cid.get(sr["content_id"])
        if not root:
            gaps.append((pkg["display_en"], 0, 0, "TOC에 루트 노드 없음"))
            continue
        from .build.registry import subtree_nodes

        sub = [n for n in subtree_nodes(root, sr["path"]) if n["content_id"]]
        have = sum(1 for n in sub if n["content_id"] in bodies_have)
        if have < len(sub):
            gaps.append((pkg["display_en"], have, len(sub), "본문 일부 미수집"))
    if gaps:
        for name, have, total, why in gaps:
            print(f"   ❌ {name:42s} 본문 {have}/{total}  {why}")
        failures.append(f"C1 서브트리 본문 결손 {len(gaps)}개")
    else:
        print("   ✅ 결손 없음")

    # C2 — 액션 0건 패키지: 항목마다 근거를 붙인다(정상/위음성 구분이 목적)
    print("\n[C2] 액션 0건 패키지 (근거 포함)")
    print(f"   {'패키지':42s} {'TOC':>4} {'본문':>4} {'등기':>4} {'액션':>4}  판정")
    zero_bad = []
    for pkg in registry["packages"]:
        name = pkg["display_en"]
        if acts_by_pkg.get(name):
            continue
        sr = pkg.get("subtree_root")
        if sr and by_cid.get(sr["content_id"]):
            from .build.registry import subtree_nodes

            sub = [n for n in subtree_nodes(by_cid[sr["content_id"]], sr["path"]) if n["content_id"]]
            n_toc = len(sub)
            n_body = sum(1 for n in sub if n["content_id"] in bodies_have)
        else:
            n_toc = n_body = 0
        registered = "O" if sr else "X"
        if n_body > 0:
            # 문서가 있는데 액션이 0건인 경우, 그것이 **설명된 0건**인지 본다.
            # 리프가 전부 비-액션으로 판정/차단됐다면 정상이다(실측: UI Agents는 13개 노드가
            # 전부 가이드 문서고 공식 액션 테이블도 없다 — 공식 문서 확인 완료).
            explained = sum(1 for t in dropped_titles if t.startswith(name + "/"))
            if explained:
                verdict = f"✓ 리프 {explained}건 전부 비-액션 판정"
            else:
                verdict = "❌ 문서가 있는데 액션 0건 (원인 불명)"
                zero_bad.append(name)
        elif pkg["kind"] == "trigger":
            verdict = "✓ 트리거(별도 경로)"
        else:
            verdict = "✓ 원본에 문서 없음"
        print(f"   {name:42s} {n_toc:>4} {n_body:>4} {registered:>4} {0:>4}  {verdict}")
    if zero_bad:
        failures.append(f"C2 문서가 있는데 액션 0건 {len(zero_bad)}개: {', '.join(zero_bad)}")

    # C3 — 단일 소스로만 등기된 항목 (다른 소스와 대조되지 않은 이름)
    print("\n[C3] 단일 소스 등기 (교차검증 없음)")
    singles = [p for p in registry["packages"] if len(p.get("sources") or []) == 1 and not p.get("subtree_root")]
    for p in singles:
        print(f"   ⚠️ {p['display_en']:42s} sources={p['sources']} kind={p['kind']}")
    print("   " + ("✅ 없음" if not singles else f"{len(singles)}건 — 이름이 한 소스에만 존재한다"))

    # C4 — 복합 액션명 잔존
    print("\n[C4] 복합 액션명 잔존")
    compound = [d for d in docs if d.get("metadata", {}).get("compound_action_title")
                and d.get("chunk_index", 0) == 0]
    seen_c = sorted({(d["package_name"], d["action_name"]) for d in compound})
    for pkg, act in seen_c[:10]:
        print(f"   ⚠️ {pkg} / {act}")
    print(f"   겸용 제목 {len(seen_c)}건 (분해된 구성 액션은 별도 행으로 존재)")

    # C5 — doc_page 규모
    print("\n[C5] doc_page(원문) 적재 규모")
    n_doc = by_type.get("doc_page", 0)
    print(f"   doc_page {n_doc}청크")
    if n_doc < args.min_doc_pages:
        failures.append(f"C5 doc_page {n_doc}청크 < 하한 {args.min_doc_pages}")
        print(f"   ❌ 하한 {args.min_doc_pages} 미달")
    else:
        print("   ✅ 하한 통과")

    # C6 — 임베딩 입력 정합
    print("\n[C6] 기본 정합")
    ids = [d["id"] for d in docs]
    dup = len(ids) - len(set(ids))
    empty = sum(1 for d in docs if not (d.get("content") or "").strip())
    print(f"   id 중복 {dup} / 빈 content {empty}")
    if dup or empty:
        failures.append(f"C6 id 중복 {dup}, 빈 content {empty}")

    print("\n" + "=" * 78)
    if failures:
        print("검증 실패:")
        for f in failures:
            print(f"  ❌ {f}")
        print("=" * 78)
        sys.exit(1)
    print("✅ 검증 통과 — 적재를 진행해도 됩니다")
    print("=" * 78)


def cmd_build_v2(args: argparse.Namespace) -> None:
    """등기부+khub 덤프 → rag_documents.jsonl (v2 규칙 계층, LLM 0콜) — [C·D 준비] 단계.

    산출 source_type: package_overview / action_schema / trigger_schema(분리 결정, 2026-07-18)
    / package_release. 이후 기존 `ingest`를 그대로 사용한다.
    """
    from .build.merge_v2 import build_documents_v2

    registry_path = config.DATA_DIR / "package_registry.json"
    if not registry_path.exists():
        sys.exit("package_registry.json이 없습니다. 먼저 registry를 실행하세요.")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    enricher = None
    if args.enrich:
        from .build.enrich_params import enrich_documents

        enricher = lambda docs: enrich_documents(  # noqa: E731
            docs, args.dump_dir, model=args.model, limit=args.enrich_limit, score_dl=args.score_dl
        )

    judger = None
    if args.judge:
        from .build.judge_actions import judge_documents

        judger = lambda docs: judge_documents(  # noqa: E731
            docs, args.dump_dir, model=args.model, limit=args.judge_limit
        )

    table_extractor = None
    if args.llm_tables:
        from .build.table_llm import extract_action_tables

        table_extractor = lambda items: extract_action_tables(items, model=args.model)  # noqa: E731

    rag_docs, stats = build_documents_v2(
        args.dump_dir, registry, chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP,
        enricher=enricher, judger=judger, table_extractor=table_extractor,
    )
    config.RAG_DOCUMENTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(config.RAG_DOCUMENTS_JSONL, "w", encoding="utf-8") as f:
        for doc in rag_docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    # stats를 파일로 남긴다 — 지금까지 유일한 소비처가 stdout print였고, 그래서 빌드가
    # 이미 세고 있던 신호(compound_action_title, skipped_non_action_titles 등)를 아무도
    # 판정에 쓰지 못했다. validate가 이 파일을 읽어 "설명된 0건"과 "조용한 0건"을 구분한다.
    stats_path = config.DATA_DIR / "build_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"RAG 문서(청크) {len(rag_docs)}개 → {config.RAG_DOCUMENTS_JSONL}")
    print(f"빌드 통계 → {stats_path}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


def cmd_build_llm(args: argparse.Namespace) -> None:
    """[재설계] 등기부+덤프 → rag_documents.jsonl. action_schema를 패키지 단위 LLM 구조화
    추출(app/rag/build/merge_llm.py)로 생성한다 — 규칙 파싱 대체 경로.

    build-v2(규칙 계층)와 나란히 존재한다. overview/release/doc_page는 같은 방식이되 doc_page는
    ko·en 양 언어 각 1행. 트리거는 'Build automations > Triggers' 트리를 따로 순회해 수집한다.
    이후 기존 validate·ingest를 그대로 사용한다.
    """
    from .build.merge_llm import build_documents_llm

    registry_path = config.DATA_DIR / "package_registry.json"
    if not registry_path.exists():
        sys.exit("package_registry.json이 없습니다. 먼저 registry를 실행하세요.")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    # 덤프 오입력/부분 생성/스키마 변경은 흔한 운영 실수다 — 그대로 읽으면 FileNotFoundError·
    # JSONDecodeError·KeyError traceback으로 죽어 원인을 알기 어렵다(validate와 같은 정책).
    dump = Path(args.dump_dir)
    toc_path = dump / "toc_en-US.json"
    if not toc_path.exists():
        sys.exit(f"{toc_path}이 없습니다 — crawl-khub를 먼저 실행하거나 --dump-dir을 확인하세요.")
    try:
        json.loads(toc_path.read_text(encoding="utf-8"))["toc"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        sys.exit(f"{toc_path} 읽기/파싱 실패({type(exc).__name__}): {exc} — 덤프가 온전한지 확인하세요.")

    rag_docs, stats = build_documents_llm(
        args.dump_dir, registry, chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP,
        model=args.model,
    )
    config.RAG_DOCUMENTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(config.RAG_DOCUMENTS_JSONL, "w", encoding="utf-8") as f:
        for doc in rag_docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    stats_path = config.DATA_DIR / "build_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"RAG 문서(청크) {len(rag_docs)}개 → {config.RAG_DOCUMENTS_JSONL}")
    print(f"빌드 통계 → {stats_path}")
    print(f"  사용 모델: {args.model or config.AGENT_PARSE_MODEL}")
    for k, v in stats.items():
        print(f"  {k}: {v}")


def _mask_secrets(target: str) -> str:
    """접속 문자열의 비밀번호를 가린다. 이 stdout은 /rag/ingest/status로 그대로 반환되므로
    유출 경로다 — 비밀번호에 '@'가 들어 있어도(`u:pa@ss@host`) 남지 않도록, userinfo의
    마지막 '@'까지 통째로 마스킹한다(`[^/]*`가 greedy라 host 직전 '@'에서 끊긴다)."""
    masked = re.sub(r"(://[^/@]*?:)[^/]*@", r"\1***@", target)  # URL 포맷(user:pass@host)
    return re.sub(r"password=\S+", "password=***", masked)  # key=value 폴백 포맷


# 코퍼스 "세대 교체" 감지 임계값 — 근거는 실측 시나리오다: v1 코퍼스(8,355행/패키지 130) 위에
# v2 산출물(1,453행/패키지 119)을 비-clean 적재하면 id/parent_id 산식이 통째로 바뀌어 겹치는
# parent가 거의 없다. delete_orphans는 "이번 빌드에 등장한 parent_id"만 훑으므로 옛 행을 한 건도
# 못 지우고, 그 행들은 유효한 임베딩을 단 채 검색에 계속 잡힌다(M3). 반대로 정상적인 부분 재적재
# (--source docs/github)는 산식이 같아 빌드 parent의 대부분이 이미 DB에 있다 — 그래서 "겹침이
# 절반도 안 되면" 세대 교체로 본다. 두 번째 임계는 "빌드가 기존 코퍼스의 절반도 못 덮는" 경우로,
# 빌드에서 제외되어 조용히 잔존하는 문서(비-액션 12건 같은)를 잡기 위한 보조 신호다.
_OVERLAP_WARN_RATIO = 0.5
_STALE_WARN_RATIO = 0.5


def _warn_if_corpus_superseded(conn, documents: list[dict], parent_ids: list[str]) -> None:
    """비-clean 적재에서 기존 코퍼스가 이번 빌드로 대체되지 않는 상황을 감지해 크게 경고한다.

    삭제 범위를 넓히지는 않는다 — 부분 재적재(--source docs/github)에서 남의 소스를 지워버리는
    사고를 막기 위한 delete_orphans의 범위 한정은 의도된 설계다. 대신 그 한정 때문에 못 지우는
    경우를 조용히 지나가지 않게 한다."""
    from .store import db

    if not parent_ids:
        return
    try:
        stats = db.corpus_overlap_stats(conn, parent_ids)
    except Exception as exc:  # 경고용 조회 실패가 적재 자체를 막지는 않게 한다
        print(f"[경고] 기존 코퍼스 통계 조회 실패 (검사 건너뜀): {exc}")
        return

    total_rows, total_parents = stats["total_rows"], stats["total_parents"]
    if not total_rows or not total_parents:
        return  # 빈 코퍼스에 처음 적재 — 비교 대상이 없다
    unseen = stats["unseen_parents"]
    overlap_ratio = (total_parents - unseen) / len(parent_ids)
    stale_ratio = unseen / total_parents

    # 비율 임계는 "세대 교체" 같은 큰 사고를 잡는 신호일 뿐이다. 정작 조용히 남는 유령 행은
    # unseen이 1개만 돼도 생긴다 — delete_orphans가 '이번 빌드의 parent_id'만 훑으므로
    # 빌드에서 빠진 parent의 행은 **한 건도** 못 지운다(2026-07-20 감사 M3·비-액션 12건).
    # 실측: 겹침 0.977 / unseen 12개(37행)여도 옛 임계(0.5)로는 아무 경고가 안 떴다.
    # 그래서 unseen > 0이면 무조건 알리고, 비율이 임계를 넘으면 문구만 격상한다.
    if unseen <= 0:
        return
    superseded = overlap_ratio < _OVERLAP_WARN_RATIO or stale_ratio >= _STALE_WARN_RATIO

    bar = "=" * 78
    print(bar)
    if superseded:
        print("[경고] 기존 코퍼스가 이번 빌드로 대체되지 않습니다 — `--clean`이 필요할 수 있습니다.")
    else:
        print(f"[경고] 이번 빌드에 없는 옛 parent {unseen}개가 DB에 남습니다 — 지워지지 않습니다.")
    print(f"  DB 기존   : {total_rows}행 / parent {total_parents}개")
    print(f"  이번 빌드 : {len(documents)}행 / parent {len(parent_ids)}개")
    print(f"  빌드 parent 중 DB에 이미 있는 것 : {total_parents - unseen}개 ({overlap_ratio:.0%})")
    print(f"  DB에만 있고 빌드에 없는 parent   : {unseen}개 ({stale_ratio:.0%})")
    print("  → id/parent_id 산식이 바뀌었거나(v1→v2) 빌드에서 제외된 문서가 있으면, 옛 행은")
    print("     지워지지 않고 유효한 임베딩을 단 채 검색에 계속 잡힙니다.")
    print("     delete_orphans는 '이번 빌드에 등장한 parent_id'만 훑으므로 이들을 못 지웁니다.")
    print("  → 전체 교체가 의도라면 `ingest --clean`으로 다시 실행하세요.")
    print(bar)


def cmd_ingest(args: argparse.Namespace) -> None:
    from .store import db

    # ── 공유 DB 보호 게이트 (2026-07-18 사고 재발 방지) ──────────────────────────
    # rag-server/.env가 RAG_DATABASE_URL(네온 등 원격)을 갖고 있으면 로컬 의도의 ingest가
    # 조용히 팀 공유 DB로 가버린다(실제 발생: --clean이 팀 코퍼스를 truncate). 접속 대상을
    # 항상 출력하고, 원격 DSN에 대한 --clean은 명시 환경변수 없이는 거부한다.
    dsn = config.database_dsn()
    is_remote = "127.0.0.1" not in dsn and "localhost" not in dsn and "host=db" not in dsn
    target = _mask_secrets(dsn)
    print(f"[ingest] 접속 대상: {target}  ({'원격/공유' if is_remote else '로컬'})")
    if args.clean and is_remote and os.getenv("RAG_ALLOW_REMOTE_CLEAN") != "1":
        sys.exit(
            "[중단] 원격/공유 DB에 --clean을 실행하려 합니다. 의도한 것이라면 "
            "RAG_ALLOW_REMOTE_CLEAN=1 환경변수를 설정하고 다시 실행하세요."
        )

    # OpenSearch도 같은 게이트를 건다 — --clean의 delete_index()는 Postgres 가드와 무관하게
    # 무조건 실행돼서, DSN만 로컬로 바꿔놓고 돌리면 공유 색인이 통째로 날아간다(M11).
    if not args.skip_opensearch:
        os_host = config.OPENSEARCH_HOST
        os_is_remote = not any(h in os_host for h in ("127.0.0.1", "localhost", "://opensearch"))
        print(f"[ingest] OpenSearch 대상: {_mask_secrets(os_host)}  ({'원격/공유' if os_is_remote else '로컬'})")
        if args.clean and os_is_remote and os.getenv("RAG_ALLOW_REMOTE_CLEAN") != "1":
            sys.exit(
                "[중단] 원격/공유 OpenSearch에 --clean(색인 삭제)을 실행하려 합니다. 의도한 것이라면 "
                "RAG_ALLOW_REMOTE_CLEAN=1 환경변수를 설정하고 다시 실행하세요."
            )

    if not config.RAG_DOCUMENTS_JSONL.exists():
        sys.exit("rag_documents.jsonl이 없습니다. 먼저 build를 실행하세요.")
    documents = [
        json.loads(line) for line in open(config.RAG_DOCUMENTS_JSONL, encoding="utf-8")
    ]

    # 빈 산출물 + --clean 가드: --clean은 "PG TRUNCATE + OpenSearch 색인 삭제"인데 넣을 문서가
    # 0개면 두 저장소를 비우고 아무것도 채우지 않는 셈이다. 여기서 한쪽만 조용히 건너뛰면 더
    # 나쁘다 — PG는 8천 행이 남고 색인만 사라져 BM25가 전멸한 채 두 저장소가 발산한다.
    # 0건은 정상 상황이 아니라 build 실패일 가능성이 훨씬 높으므로, 어느 쪽도 건드리지 않고
    # 여기서 중단한다(연결 전에 판단하므로 트랜잭션도 열리지 않는다).
    if not documents:
        if args.clean:
            sys.exit(
                "[중단] rag_documents.jsonl이 비어 있습니다(0건). --clean은 기존 코퍼스(pgvector + "
                "OpenSearch)를 전부 지우고 0건을 적재하게 되므로 실행하지 않았습니다. "
                "build가 정상적으로 문서를 생성했는지 먼저 확인하세요."
            )
        print("[경고] rag_documents.jsonl이 비어 있습니다(0건) — 적재/색인할 문서가 없습니다. build 결과를 확인하세요.")

    conn = db.connect()
    orphan_ids: list[str] = []
    try:
        db.ensure_schema(conn)
        if args.clean:
            to_embed = documents
        else:
            # 재크롤링/재적재해도 upsert가 id로 덮어써서 row 중복은 안 생기지만, 내용이
            # 하나도 안 바뀐 문서까지 매번 재임베딩하는 건 순수 비용 낭비였다 — content_hash가
            # 저장된 것과 같은 문서는 임베딩만 건너뛴다. title/url/metadata는 content가 같아도
            # 바뀔 수 있으므로 DB upsert와 OpenSearch 색인은 전체 문서 기준으로 수행한다.
            existing_hashes = db.get_content_hashes(conn, [d["id"] for d in documents])
            conn.commit()
            to_embed = [d for d in documents if existing_hashes.get(d["id"]) != db.content_hash(d["content"])]
            skipped = len(documents) - len(to_embed)
            if skipped:
                print(f"내용이 안 바뀐 문서 {skipped}개는 재임베딩만 건너뜁니다 (전체 {len(documents)}개 중).")
    finally:
        conn.close()

    embeddings = None
    if to_embed and not args.skip_embedding:
        from .retrieval.embed import embed_texts

        print(f"임베딩 생성 중 ({config.EMBEDDING_PROVIDER}/{config.EMBEDDING_MODEL}, {len(to_embed)}개)...")
        new_embeddings = embed_texts(
            [d["content"] for d in to_embed],
            on_progress=lambda done, total: print(f"  {done}/{total}"),
        )
        embeddings_by_id = {doc["id"]: emb for doc, emb in zip(to_embed, new_embeddings)}
        embeddings = [embeddings_by_id.get(doc["id"]) for doc in documents]

    # 임베딩 생성(수 분 소요 가능) 동안 커넥션을 열어두면 Neon pooler가 유휴 SSL 연결을
    # 끊어(SSLError: connection has been closed unexpectedly) upsert 시점에 죽는다 —
    # 임베딩이 끝난 뒤 새 커넥션으로 붙는다(RPA-213).
    conn = db.connect()
    try:
        # ── 파괴적 작업: 임베딩이 끝난 뒤에 한다 ──────────────────────────────────
        # --clean은 clear_all(TRUNCATE+커밋)로 초기화한다. 비-clean은 이번 빌드에 없는
        # 고아 row만 지운다 — 문서가 사라지거나 청크 수가 줄면 옛 청크가 유효한 임베딩을
        # 단 채 검색에 계속 잡히기 때문(M1/M3).
        if args.clean:
            print("--clean: 기존 rag_documents 전체 삭제")
            db.clear_all(conn)
        elif documents:
            parent_ids = sorted({p for p in (d.get("parent_id", d["id"]) for d in documents) if p})
            _warn_if_corpus_superseded(conn, documents, parent_ids)
            orphan_ids = db.delete_orphans(conn, [d["id"] for d in documents], parent_ids)
            if orphan_ids:
                print(f"이번 빌드에 없는 고아 row {len(orphan_ids)}개 삭제")
        count = db.upsert_documents(conn, documents, embeddings) if documents else 0
        print(f"pgvector 적재 완료: {count}개")
    finally:
        conn.close()

    if args.skip_opensearch:
        # 고아는 pgvector에서 이미 커밋돼 사라졌다 — 여기서 id를 그냥 버리면 어떤 문서가
        # 색인에만 남았는지 영영 알 수 없다. 지울 수 없으니 최소한 남긴다.
        if orphan_ids:
            print(
                f"[경고] --skip-opensearch: pgvector에서 지운 고아 {len(orphan_ids)}개가 OpenSearch "
                "색인에 그대로 남습니다 (BM25에만 옛 문서가 잡힘). --skip-opensearch 없이 다시 "
                f"적재하거나 아래 id를 수동 삭제하세요: {orphan_ids[:20]}"
            )
        return

    from .store import opensearch_client

    os_client = opensearch_client.connect()
    if args.clean:
        print("--clean: 기존 OpenSearch 색인 삭제")
        opensearch_client.delete_index(os_client)
    opensearch_client.ensure_index(os_client)
    # pgvector에서 지운 고아를 색인에서도 지운다 — 안 지우면 BM25에만 옛 문서가 살아남는다.
    # 색인(bulk_index)보다 먼저 지운다: bulk_index가 끝내 실패하면 예외로 빠져나가는데,
    # 뒤에 두면 그때 삭제가 아예 실행되지 않아 PG에 없는 행이 색인에 영구히 남는다.
    # 고아 id는 정의상 이번 documents의 id와 겹치지 않으므로 방금 넣을 문서를 지울 위험은 없다.
    if orphan_ids:
        deleted = opensearch_client.delete_by_ids(os_client, orphan_ids)
        print(f"OpenSearch 고아 삭제: {deleted}개")
    os_count = opensearch_client.bulk_index(os_client, documents) if documents else 0
    print(f"OpenSearch 색인 완료: {os_count}개")


def main() -> None:
    # Windows 콘솔 기본 코드페이지(cp949 등)는 em-dash(—)/en-dash(–) 같은 문자를
    # 인코딩 못 해 print()가 UnicodeEncodeError로 죽는다(실측: 크롤링한 문서 내용을
    # 그대로 출력하는 search 명령에서 재현됨 — 외부 문서 텍스트는 어떤 특수문자가
    # 들어있을지 통제할 수 없다). UTF-8로 강제하고, 그래도 콘솔이 못 그리는 문자는
    # errors="replace"로 안전하게 대체해 최소한 크래시는 나지 않게 한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="python -m app.rag.pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_crawlk = sub.add_parser("crawl-khub",
                              help="[v2] khub 원문 덤프 생성 (toc_*.json + bodies_*.jsonl[html], 주+보조맵)")
    p_crawlk.add_argument("--dump-dir", required=True, help="덤프 출력 디렉터리")
    p_crawlk.add_argument("--locales", default="ko-KR,en-US", help="쉼표 구분 로케일 (기본 ko-KR,en-US)")
    p_crawlk.add_argument("--delay", type=float, default=0.12, help="요청 간 지연(초)")
    p_crawlk.add_argument("--limit", type=int, default=0, help="맵마다 앞 N개만 (0=전체, 스모크용)")
    p_crawlk.set_defaults(func=cmd_crawl_khub)

    p_registry = sub.add_parser(
        "registry",
        help="[v2] khub 덤프(ToC+본문)에서 패키지 등기부(package_registry.json) 생성 — 3소스 합집합",
    )
    p_registry.add_argument("--dump-dir", required=True, help="khub-dump 디렉터리 (toc_*.json, bodies_*.jsonl)")
    p_registry.set_defaults(func=cmd_registry)

    p_build2 = sub.add_parser(
        "build-v2",
        help="[v2] 등기부+덤프 → rag_documents.jsonl (규칙 1단은 LLM 0콜, --enrich 시 파라미터 LLM 보강 2단 수행)",
    )
    p_build2.add_argument("--dump-dir", required=True, help="khub-dump 디렉터리")
    p_build2.add_argument("--enrich", action="store_true", help="uicontrol 후보 행의 파라미터를 LLM으로 보강 (캐시 사용)")
    p_build2.add_argument("--enrich-limit", type=int, default=0, help="보강 대상 상한 (0=무제한, 스모크용)")
    p_build2.add_argument(
        "--judge", action="store_true",
        help="공식 액션 테이블에 없는 리프를 LLM으로 '진짜 액션인가' 판별 — 비-액션은 방출에서 제외 "
             "(제목 마커로는 원리적으로 못 잡는 절차형 문서 대응)",
    )
    p_build2.add_argument("--judge-limit", type=int, default=0, help="판별 대상 상한 (0=무제한, 스모크용)")
    p_build2.add_argument(
        "--llm-tables", action="store_true",
        help="패키지 개요의 공식 액션 목록을 LLM으로 추출 — 헤더 표기(Action/Operation)·표 개수 같은 "
             "구조 규칙에 의존하지 않는다. 규칙 파싱 결과와 합집합을 취하고 차이를 통계로 남긴다",
    )
    p_build2.add_argument("--score-dl", action="store_true", help="dl 규칙 결과를 골드로 LLM 추출 정합 채점(행 수정 없음)")
    p_build2.add_argument("--model", default=None, help="보강용 챗 모델 (기본: AGENT_PARSE_MODEL)")
    p_build2.set_defaults(func=cmd_build_v2)

    p_buildllm = sub.add_parser(
        "build-llm",
        help="[재설계] 등기부+덤프 → rag_documents.jsonl. action_schema를 패키지 단위 LLM 구조화 "
             "추출로 생성(규칙 파싱 대체). doc_page는 ko·en 양 언어, 트리거는 별도 트리 순회",
    )
    p_buildllm.add_argument("--dump-dir", required=True, help="khub-dump 디렉터리")
    p_buildllm.add_argument("--model", default=None, help="추출 챗 모델 (기본: AGENT_PARSE_MODEL)")
    p_buildllm.set_defaults(func=cmd_build_llm)

    p_validate = sub.add_parser(
        "validate",
        help="[v2] 빌드 산출물 품질 게이트 — 위반 시 종료 코드 1로 적재를 막는다",
    )
    p_validate.add_argument("--dump-dir", required=True, help="khub-dump 디렉터리")
    p_validate.add_argument(
        "--min-doc-pages", type=int, default=1000,
        help="doc_page 청크 하한 (기본 1000) — 원문 방출이 조용히 죽는 것을 막는다",
    )
    p_validate.set_defaults(func=cmd_validate)

    p_ingest = sub.add_parser("ingest", help="임베딩 생성 후 pgvector + OpenSearch 적재")
    p_ingest.add_argument("--skip-embedding", action="store_true")
    p_ingest.add_argument("--skip-opensearch", action="store_true")
    p_ingest.add_argument(
        "--clean", action="store_true",
        help="적재 전 기존 rag_documents 테이블/OpenSearch 색인을 전부 비운다 "
             "(upsert는 새 build에 없는 옛 row를 안 지우므로, RAG 구조를 크게 바꾼 뒤 재적재할 때 필요)",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
