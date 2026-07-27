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

## 주의: P4는 일부 진행 중

fusion 결합 방식(RRF vs 정규화 융합) 최종 검증은 아직 진행 중 — 현재 proxy 지표로는 정규화 융합이 우세해 보이지만, 실제 RAGAS Context Precision/Recall로 재검증 중이며 완료 전까지는 "확정"이 아니라 "진행 중"으로 표시해뒀음. 재검증 완료되면 P4 업데이트 필요.
