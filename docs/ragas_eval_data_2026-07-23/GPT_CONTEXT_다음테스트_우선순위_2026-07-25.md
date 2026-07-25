# 다음 테스트 우선순위 결정 요청 — GPT에게 전달할 컨텍스트

## 전달할 엑셀 파일 (6개, 같은 폴더)

1. **`chunk_size_comparison_EXAONE40_2026-07-25_v2.xlsx`** (원본은 잠겨있어서 v2가 최신 —
   v1은 무시) — 14개 chunk_size 후보(토큰 9 + 글자 5) 전체 RAGAS 5대 지표 비교표,
   토큰/글자 정렬, 문서유형 3분류(doc_page/action_schema-jar/llm_agent), 질문유형 4종
2. **`paired_significance_test_2026-07-25.xlsx`** — char vs token 실제길이 매칭 4쌍 +
   2ⁿ가설 1쌍(tok1000 vs 1024) = 25개 케이스레벨 paired 통계검정 결과
3. **`deep_chunk_eda_2026-07-25.xlsx`** — chunks_per_doc, split/unsplit 분리 통계,
   문서별 chars_per_token 비율, 검색전용 지표(Hit@K/MRR/evidence_coverage)
4. **`gold_boundary_check_2026-07-25.xlsx`** — 골드셋 근거 스니펫이 청크 경계에서
   잘리는지 여부
5. **`raw_document_length_2026-07-25.xlsx`** — 청킹 전 원문 길이 분포(카테고리별)

## 실험 배경

- 목표: A360 RAG 파이프라인의 chunk_size(및 이후 검색 하이퍼파라미터) 결정
- 이전 단계(gpt-4o-mini 기준, 별도 세션): char 기준 그리드에서 cs1200/10%overlap이
  대체로 최고, hybrid+reranker가 vector-only보다 확실히 좋음 — 확정된 사실
- 이번 단계: 로컬 GPU(EXAONE-4.0-32B, 비용 $0, reasoning 항상 OFF)로 생성+채점 둘 다
  돌려서 129건 골드셋 전체를 무제한 반복 가능하게 함
- **로컬 judge 자체의 신뢰성은 이미 기각됨**(무관한 오답도 관대하게 통과시킴, grammar
  강제해도 판단 부실 — 실측 확인) → 이번 단계 judge는 **gpt-4o-mini 고정**, 로컬은
  생성에만 사용

## 지금까지 확인된 핵심 결론 (파일 1~5에 근거)

1. char vs token splitter 자체는 **통계적으로 유의미한 성능 차이 없음**(25개 paired
   검정 전부 Holm 보정 후 불유의) — 실제 청크 길이·미분할 비율·문서유형이 진짜 변수.
2. 청크가 클수록 context_recall·evidence_coverage 상승, **그러나 Hit@1/MRR은 오히려
   작은 청크가 유리**(방향 반대) — 최종 생성 품질은 evidence_coverage 쪽 경향을 따름.
3. action_schema(특히 jar)는 원문 자체가 짧아서(median 216.5토큰) 900토큰 근방부터
   "미분할 포화" 상태 — chunk_size가 커도 이득이 없는 게 아니라 애초에 무관해짐.
   doc_page는 원문이 훨씬 길어(median 807토큰, 44.6%가 900토큰 초과) chunk_size가
   실질적으로 의미 있는 쪽.
4. 현재 방어적 선택: **tok900~tok1024 대(vector-only 스크리닝 기준)**, 또는 기존
   cs1200 유지도 합리적 — tok2048은 "포화"라 실익 작음.
5. cs300_ov0/cs1500_ov0 두 후보만 코퍼스 93건(전체 4680건 중, action_schema-llm_agent)
   결측 — 원인 규명 완료(스냅샷 타이밍 차이, 버그 아님), 골드셋 영향은 1/129건뿐.

## 지금 상태 / 제약

- **overlap 변형**: char 기준(cs300/600/900/1200/1500)은 이미 DB에 overlap>0 조합
  8개(ov30/60/90/120/150/180/240/300)가 만들어져 있으나 **RAGAS 평가는 아직 안 함**.
  토큰 기준은 overlap>0 자체를 아직 하나도 안 만듦.
- **새 chunk_size 후보(예: tok500) 추가 생성은 이 컴퓨터에서 불가능** — 원본 크롤링
  코퍼스(`rag-server/data/docs.jsonl` 등)가 없음. 원본 컴퓨터에서 가져와야 함.
- **hybrid search(BM25+vector+reranker) 실험 인프라는 아직 코드에 없음** — OpenSearch
  색인·RRF 결합·Voyage reranker 연동을 새로 만들어야 함. **사용자는 이 실험 범위를
  OpenSearch 공식 문서에 나온 하이브리드 검색 방식으로 한정할 계획**(임의 RRF/EWA
  가중치 그리드서치 아님).
- **GPU 자원**: 로컬 LLM이라 비용 $0, **오늘부터 며칠간 계속 GPU를 돌릴 수 있음** —
  시간이 병목이 아니라 "무엇을 돌릴지 우선순위"가 병목.
- 케이스당 채점 약 1분(RAGAS 5개 지표, gpt-4o-mini judge), 129건 기준 후보 1개당
  약 2~2.5시간.

## 질문

위 상황에서, 남은 GPU 시간(며칠)을 다음 후보군에 어떤 우선순위로 배분해야 할지:
(a) char overlap 8개 변형 RAGAS 평가, (b) 상위 후보(tok900/1024, cs1200 등) 반복실행으로
재현성/노이즈 확인, (c) OpenSearch 공식 하이브리드 검색 방식 실험 준비, (d) 그 외
제안이 있다면?
