# PPT 1페이지(핵심결론) 근거 자료

`GPT_PPT_1_핵심결론_2026-07-27.md`에 나온 4가지 숫자 주장의 원본 데이터.

| 파일 | 어떤 주장을 뒷받침하나 |
|---|---|
| `1_overlap실험_gpt4o_mini_2026-07-21.xlsx` | overlap 0%→10%→20% 스윕 원본 (gpt-4o-mini, 129건) — "overlap 0→10% 개선, 10→20% 하락" 근거 |
| `2_chunk_size별_검색지표_2026-07-25.xlsx` | 14개 후보 전체의 Hit@K/MRR/EvidenceCoverage — "300→1200자 +20.4%p", "1200→1500자 포화" 근거 |
| `3_유의성검정_115개_2026-07-25_26.xlsx` | vector-only 25개 + hybrid+rerank 59개 + McNemar 31개 = 115개 검정 원본 — "115개 검정 중 방향 안 뒤집힘" 근거 |
| `4_chunk_size비교_로컬EXAONE_2026-07-25.xlsx` | 14개 후보 RAGAS 5종 전체(Faithfulness/AnswerRelevancy/ContextPrecision/ContextRecall/AnswerCorrectness), 로컬 EXAONE 생성+채점 — gpt-4o-mini(1번 파일)와 별개로 로컬 모델로도 같은 스크리닝 재현 |
| `5_overlap재확인_로컬EXAONE_hybrid_rerank_2026-07-26.xlsx` | cs1200_ov0 vs cs1200_ov120(10%), 로컬 EXAONE+hybrid+rerank 기준 — gpt-4o-mini 스윕(1번 파일)과 별개로 overlap 10%가 로컬 모델·hybrid+rerank 파이프라인에서도 손해 없음을 재확인 |
