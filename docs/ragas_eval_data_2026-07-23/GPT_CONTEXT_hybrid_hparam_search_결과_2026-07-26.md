# hybrid 하이퍼파라미터 단계적 탐색 결과 — GPT에게 전달할 컨텍스트 (2026-07-26, 3차)

지난 상담(GPT)이 제안한 "전수 교차 대신 단계적 탐색" 설계를 그대로 따라 실행했습니다.
(pool 포화점 → fusion 방식 → Pareto 선정 → reranker_pool×top_k → 최종 생성 평가)

## 전달할 파일

**`hybrid_hparam_search_full_2026-07-26.xlsx`** — 시트 5개
1. `1_최종비교`: anchor(기존 RRF60/pool50/rerank20) vs 이번에 찾은 승자 2개, 129건 전체
2. `2_유의성검정`: anchor vs 승자 2개 paired 검정(bootstrap CI + Wilcoxon + Holm)
3. `3_1단계_pool포화점`: BM25/Vector branch pool 10~200 포화 곡선
4. `4_2단계_fusion비교`: RRF 5종 vs normalized(min_max+arithmetic_mean) 3종, pool 100/150
5. `5_4단계_reranker풀xtopk`: reranker_pool 10/20/50/100 × top_k 3/5/7/10 교차

## 실행 요약 (제안하신 설계 그대로)

1. **1단계 pool 포화점** (tok1024, BM25/Vector 각 top-200 한 번만 뽑아서 컷): union recall/evidence_coverage 증가폭이 129건 기준 "1건 이하"로 떨어지는 지점이 **50→100 전환**이었고, 150→200은 완전히 0 — **pool 100, 150 두 개를 유지**로 결정.
2. **2단계 fusion 비교** (pool 100/150 × RRF 5종 + normalized 3종 = 16개): **normalized(min_max+arithmetic_mean)가 RRF를 전 지표에서 앞섬** — 특히 BM25 가중치를 높인 조합(0.5~0.7)이 가장 강함. 현재 프로덕션 기본값(RRF, k=60, 1:1 가중)이 테스트한 것 중 가장 약한 축에 속함.
3. **3단계 Pareto 선정**: ranking형(w_bm25=0.7/w_vec=0.3, MRR·Hit@1 최고) + coverage형(w_bm25=0.5/w_vec=0.5, evidence_coverage 최고) 2개로 압축.
4. **4단계 reranker_pool×top_k** (2개 fusion × pool 10/20/50/100, 케이스당 rerank 1회만 호출, top_k 3/5/7/10은 그 결과를 자르기만 함, 총 1032회 실호출·실패 0건): **pool 50과 100이 완전히 동일한 결과** → pool 100은 추가 이득 없이 비용만 더 듦 → **reranker_pool=50 채택**. pool 20(현재 프로덕션 기본값)은 pool 50 대비 hit@10·MRR 등에서 뚜렷이 낮음.
5. **5단계 최종 생성+RAGAS 평가** (EXAONE-4.0 생성+채점, tok1024, 129건 전체, anchor는 기존 실행 결과 재사용 — 재실행 안 함): fusion_pool=150, rerank_candidates=50, top_k=5(anchor와 동일하게 고정)로 ranking형·coverage형 두 설정을 새로 실행.

## 핵심 결과 — 정직하게: "더 나아 보이지만, 통계적으로 확정은 안 됨"

| 지표 | anchor | ranking형 | coverage형 |
|---|---|---|---|
| hit@3 | 0.9147 | 0.9380 | 0.9380 |
| hit@5 | 0.9225 | 0.9535 | 0.9612 |
| MRR | 0.7991 | 0.8114 | 0.8172 |
| evidence_coverage | 0.9147 | 0.9457 | 0.9457 |
| faithfulness | **0.9268** | 0.9087 | 0.9190 |
| answer_correctness | 0.6828 | 0.6920 | 0.6911 |

- **retrieval 계열 지표(hit@3/5, MRR, evidence_coverage, context_recall)는 anchor 대비 방향성 있게 개선** — ranking형·coverage형 둘 다 같은 방향.
- **faithfulness는 오히려 anchor가 더 높음**(두 신규 설정 모두 -1~2%p, 유의하지 않음) — 검색 품질 개선이 반드시 생성 충실도로 이어지지는 않는다는 이전 단계(vector-only→hybrid+rerank)의 패턴과 동일한 결이 여기서도 보임.
- **anchor vs 승자 2개, 승자끼리 3개 조합 × 10개 지표 = 28개 유효 검정, Holm 보정 후 유의 0개.** 129건 골드셋의 통계적 검출력 한계에 도달한 것으로 보임 — 이전 상담에서 GPT가 경고한 "하이퍼파라미터 과적합·해석 불가능성" 우려가 실제로 여기서 확인됨: 지표상으론 나아 보이는 차이가 유의성 검정을 통과하지 못함.

## 지금 상태 / 결정 필요한 것

- 아직 안 한 것(원래 계획): **6단계 — cs1200_ov120에서 최종 설정 재현**(청크 표현 방식에 강건한지 확인)
- GPT가 이전 상담에서 이미 언급한 대로, **129건이라는 표본 크기 자체가 병목**입니다. 지금 시점에서 추가로 할 수 있는 것:
  (a) 이대로 6단계(cs1200_ov120 재현)까지 마무리
  (b) 반복실행(생성+채점만 2~3회, 검색은 결정론적이라 반복 불필요)으로 노이즈 폭 자체를 추정
  (c) **새 골드 질문 20~30개 추가** — GPT가 이전 상담에서 "RRF constant나 pool 후보를 더 늘리는 것보다 정보가치가 크다"고 이미 제안한 항목, 아직 착수 안 함

## 질문

이 결과(특히 "지표는 나아 보이지만 128건 표본에서 통계적으로 확정 안 됨")를 감안했을 때,
(a) 그래도 이 설정(coverage형 또는 ranking형)을 프로덕션 후보로 채택할 근거가 되는지,
(b) 채택 전에 골드셋 확장이 정말 우선순위가 더 높은지,
(c) 아니면 이 정도 방향성 확인으로 충분하고 다른 곳(예: 생성 프롬프트 품질)으로 넘어가야 하는지?
