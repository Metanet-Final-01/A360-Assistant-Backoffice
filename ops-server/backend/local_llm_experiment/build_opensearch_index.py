"""로컬 eval 후보 테이블(rag_documents_eval_{name})을 로컬 docker OpenSearch
(a360-opensearch, localhost:9200)에 같은 이름의 별도 인덱스로 색인한다.
실험용 인덱스를 십수 개 만들 것이므로 공유 Bonsai(프로덕션과 공유, 유료)는 건드리지
않고 로컬 전용으로 격리한다 - docker-compose.yml의 local-opensearch 프로파일 컨테이너.
source_type/package_name/action_name은 Neon rag_documents에서 parent_id로 조인해 채운다
(로컬 eval 후보 테이블 자체엔 이 컬럼들이 없어서).
"""
import argparse
import sys

import psycopg
from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

LOCAL_DSN = "host=127.0.0.1 port=5432 dbname=a360 user=a360_admin password=a360_local_password"
NEON_DSN = "postgresql://neondb_owner:npg_BeZ3MdKR7IGE@ep-young-river-ao9slvcl-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
OPENSEARCH_HOST = "http://localhost:9200"

INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "filter": {
                "korean_cjk_bigram": {"type": "cjk_bigram"},
                "english_stop": {"type": "stop", "stopwords": "_english_"},
            },
            "analyzer": {
                "korean_cjk": {
                    "type": "custom", "tokenizer": "standard",
                    "filter": ["cjk_width", "lowercase", "korean_cjk_bigram", "english_stop"],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "source_type": {"type": "keyword"},
            "package_name": {"type": "keyword"},
            "action_name": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "korean_cjk", "fields": {"raw": {"type": "keyword"}}},
            "content": {"type": "text", "analyzer": "korean_cjk"},
            "parent_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
        }
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table-name", required=True, help="예: rag_documents_eval_tok900_ov0")
    ap.add_argument("--index-name", default=None, help="생략 시 table-name과 동일")
    ap.add_argument("--recreate", action="store_true", help="이미 있으면 지우고 새로 만듦")
    args = ap.parse_args()
    index_name = args.index_name or args.table_name

    local_conn = psycopg.connect(LOCAL_DSN)
    lcur = local_conn.cursor()
    lcur.execute(f"SELECT id, parent_id, chunk_index, title, content FROM {args.table_name}")
    rows = lcur.fetchall()
    parent_ids = list({r[1] for r in rows})
    local_conn.close()
    print(f"{args.table_name}: {len(rows)}건 로드, distinct parent {len(parent_ids)}건")

    neon_conn = psycopg.connect(NEON_DSN)
    ncur = neon_conn.cursor()
    ncur.execute(
        "SELECT parent_id, source_type, package_name, action_name FROM rag_documents WHERE parent_id = ANY(%s)",
        (parent_ids,),
    )
    meta_by_parent = {row[0]: {"source_type": row[1], "package_name": row[2], "action_name": row[3]} for row in ncur.fetchall()}
    neon_conn.close()
    print(f"Neon에서 메타데이터 {len(meta_by_parent)}건 조회")

    client = OpenSearch(
        hosts=[OPENSEARCH_HOST],
        use_ssl=False, verify_certs=False, http_compress=True, timeout=30,
    )

    if client.indices.exists(index=index_name):
        if args.recreate:
            client.indices.delete(index=index_name)
            print(f"기존 인덱스 {index_name} 삭제 후 재생성")
        else:
            print(f"인덱스 {index_name}이 이미 있음 — 그대로 재색인(문서 upsert). --recreate로 전체 삭제 가능")
    if not client.indices.exists(index=index_name):
        client.indices.create(index=index_name, body=INDEX_BODY)

    def _actions():
        for doc_id, parent_id, chunk_index, title, content in rows:
            meta = meta_by_parent.get(parent_id, {})
            yield {
                "_op_type": "index", "_index": index_name, "_id": doc_id,
                "_source": {
                    "id": doc_id, "parent_id": parent_id, "chunk_index": chunk_index,
                    "title": title or "", "content": content,
                    "source_type": meta.get("source_type"), "package_name": meta.get("package_name"),
                    "action_name": meta.get("action_name"),
                },
            }

    success, errors = bulk(client, _actions(), raise_on_error=False)
    print(f"색인 완료: {success}건 성공, 실패 {len(errors) if errors else 0}건")
    client.indices.refresh(index=index_name)
    count = client.count(index=index_name)["count"]
    print(f"최종 인덱스 문서 수: {count}")


if __name__ == "__main__":
    sys.exit(main())
