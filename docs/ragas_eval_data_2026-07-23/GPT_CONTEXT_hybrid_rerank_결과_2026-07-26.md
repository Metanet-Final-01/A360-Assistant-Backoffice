# hybrid+rerank 실험 결과 — GPT에게 전달할 컨텍스트 (2026-07-26)

이전 상담(`GPT_CONTEXT_다음테스트_우선순위_2026-07-25.md`)에서 GPT가 제안한 우선순위 중
"하이브리드 검색 인프라 준비"를 실행하고 실제로 돌린 결과입니다.

## 전달할 파일 (같은 폴더)

1. **`hybrid_rerank_comparison_2026-07-26.xlsx`** — 시트 2개
   - `vector_vs_hybrid_rerank`: cs1200_ov0/cs1200_ov120/tok900/tok1024, vector-only vs
     hybrid+rerank 비교 (RAGAS 5대 지표 + Hit@1/3/5/MRR/evidence_coverage)
   - `paired_significance_4cand`: hybrid+rerank 적용 후 4개 후보 간 전수 쌍(6쌍×10지표=60개)
     paired 유의성검정 (bootstrap 95%CI + Wilcoxon + Holm 보정)

## 실험 배경 (지난 상담 이후 달라진 것)

- 지난 상담 시점엔 "hybrid+rerank는 gpt-4o-mini로만 검증됐고, token 기반은 아예 테스트
  안 됨"이 갭이었음 (사용자 직접 지적: "token 기반으로는 잡지도 않아서 실험이나 똑바로 해")
- 이번에 로컬 EXAONE-4.0(생성+채점 모두, reasoning off, $0)로 hybrid+rerank(pgvector 벡터
  + OpenSearch BM25 → RRF 융합(rrf_k=60, pool=50, rerank_candidates=20, 프로덕션과 동일
  공식) → Voyage rerank-2.5-lite) 파이프라인을 처음부터 구현해서 4개 후보(char 2개:
  cs1200_ov0/cs1200_ov120 — gpt-4o-mini 우승조합 재현용, token 2개: tok900/tok1024 — 신규)에
  129건 골드셋 전체를 돌림
- 로컬 OpenSearch는 공유 프로덕션 Bonsai가 아니라 별도 docker 컨테이너(a360-opensearch)에
  후보별 인덱스를 만들어 격리함(프로덕션 검색 인프라에 영향 없음)

## 핵심 결과

1. **hybrid+rerank가 vector-only를 압도적으로 이김** — 4개 후보 전부 동일 패턴:
   - hit@1: 0.33 → 0.68 (약 2배)
   - MRR: 0.44 → 0.79
   - context_precision: 0.73 → 0.94
   - context_recall: 0.86 → 0.98
   - evidence_coverage: 0.70 → 0.90
   - faithfulness: 0.84 → 0.90~0.93 (완만한 개선)
   - answer_correctness: 0.62 → 0.68~0.69 (완만한 개선)
   → gpt-4o-mini 단계의 결론("hybrid+rerank가 확실히 낫다")이 **로컬 모델·char·token
   전부에서 재현됨**. token 기반 갭이 이제 해소됨.

2. **hybrid+rerank 적용 후엔 4개 후보(cs1200_ov0/cs1200_ov120/tok900/tok1024) 사이에
   유의미한 차이가 전혀 없음** — 6쌍×10지표=60개 검정 중 Holm 보정 후 유의 0개(1개는
   완전 동점이라 검정 불가로 제외, 59개 중 0개). vector-only 단계에서 나온 "char vs
   token은 실제 청크 길이만 맞으면 차이 없다"는 결론이 hybrid+rerank를 씌운 뒤엔 더
   강하게 확인됨 — retrieval 품질이 올라가면 chunk_size 자체의 미세한 차이는 신호가
   거의 안 남는 수준으로 묻힘.

## 지금 상태 / 남은 선택지

- 이 4개 중 어느 걸 최종으로 확정해도 성능상 손해는 없어 보임(통계적으로 구분 불가) —
  선택 기준을 다른 데서 찾아야 하는 상황(스토리지/임베딩 비용, 기존 검증 이력 등)
- 아직 안 한 것: (a) 최종 후보 반복실행으로 재현성/노이즈 확인, (b) char overlap 8개
  변형(cs300/600/900/1200/1500 각 overlap>0)을 hybrid+rerank까지 확장, (c) 실제
  프로덕션 모델(gpt-5.4-mini)로 최종 후보 재검증
- GPU는 계속 무료로 돌릴 수 있는 상태 유지 중

## 질문

이 결과를 바탕으로, 다음 우선순위를 어떻게 잡는 게 좋을지:
(a) 4개 중 하나를 최종 확정하고 반복실행 재현성 체크, (b) overlap 변형까지 hybrid+rerank
로 확장, (c) 프로덕션 모델(gpt-5.4-mini)로 재검증, (d) 그 외 제안이 있다면?
