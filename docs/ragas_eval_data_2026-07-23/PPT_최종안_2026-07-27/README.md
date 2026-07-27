# RAG 발표 장표 — 페이지별 상세 문서

GPT와 논의한 4페이지+부록 구성을 따름.

| 파일 | 역할 |
|---|---|
| `P1_최종파이프라인.md` | 최종 검색 파이프라인 + 확정값 |
| `P2_chunk_overlap_EDA.md` | chunk_size·overlap 결정 근거 |
| `P3_검색방식별_성능비교.md` | Vector·BM25·Hybrid·Reranker 기여도 |
| `P4_candidatepool_topk_gridsearch.md` | candidate pool·top-k·결합방식 탐색 |
| `부록_char_vs_token_및_상세자료.md` | char vs token, 2ⁿ 무의미, 상세자료 위치 |
| `그래프/` | 각 페이지에 삽입된 차트 원본(8개) |

## 업데이트(2026-07-27): P4 fusion 방식 재검증 완료

16개 fusion 설정 전체를 실제 RAGAS Context Precision/Recall로 재채점 완료. **정규화 융합(w_bm25=0.5/w_vector=0.5)이 16개 중 1위(HM 0.8923)로 확정.** 흥미롭게도 1단계 proxy 지표(MRR/Hit@1)로 골랐던 다른 가중치 조합(w_bm25=0.7/w_vector=0.3)은 실제 지표로는 16개 중 꼴찌였음 — proxy 지표가 잘못된 신호를 준 사례. P1/P4 모두 확정 내용으로 업데이트 완료.
