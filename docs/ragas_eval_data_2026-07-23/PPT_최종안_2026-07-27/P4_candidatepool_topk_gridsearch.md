# P4. Candidate Pool · Top-K · 결합 방식 탐색

*실측: 로컬 EXAONE-4.0, tok1024 기준, 129건 전체. 단계적 탐색(pool 포화점→fusion 방식→reranker_pool×top_k)으로 진행, 전수교차 아님.*

---

## Candidate pool — 100 근방부터 포화

![pool 포화점](그래프/P4_pool_포화점.png)

```text
10~50  → 검색 후보 부족, 늘릴수록 뚜렷이 개선
100    → Union Gold Recall 99.2%, EvidenceCoverage 96.1% — 이후 증가폭 129건 기준 1건 이하
150~200 → 100 대비 추가 이득 사실상 없음
```

→ **pool=100~150이 "늘리는 이득이 있는 마지막 지점"**, 200까지 늘릴 필요는 없음.

---

## Reranker pool × Final top-k

![reranker pool topk](그래프/P4_reranker_pool_topk.png)

```text
reranker_pool 10  → 명확히 열세
reranker_pool 20  → 현재 운영값, 나쁘지 않음
reranker_pool 50  → 20 대비 개선
reranker_pool 100 → 50과 결과 완전히 동일(추가 이득 0, latency만 증가)
```

→ **reranker_pool=50이면 충분, 100은 비용만 늘어남.**

---

## 결합 방식(Fusion) — 실제 Context Precision/Recall로 재검증 완료

1단계 스크리닝은 자체 evidence_coverage/MRR 지표 기준이었는데, 이게 실제 RAGAS Context Precision/Recall과 완전히 일치하지 않을 수 있다는 우려가 있어 **16개 fusion 설정 전체를 생성 없이 실제 Context Precision/Recall로 재채점**했다(2026-07-27).

| 순위 | 방식 | Precision | Recall | HM |
|---|---|---|---|---|
| 1위 | **정규화 융합, pool=150, w_bm25=0.5/w_vec=0.5** | 0.8509 | 0.9380 | **0.8923** |
| 2위 | 정규화 융합, pool=100, w=0.5/0.5 | 0.8498 | 0.9380 | 0.8917 |
| 중위권 | RRF 계열(현 프로덕션 기본값, k=60) | 0.827 | 0.942~0.950 | 0.884 |
| **16위(꼴찌)** | 정규화 융합, w_bm25=0.7/w_vec=0.3 | 0.8131 | 0.9070 | 0.8575 |

**중요한 반전**: 1단계 proxy 지표(MRR/Hit@1)로는 w_bm25=0.7/w_vec=0.3("ranking형")이 가장 좋아 보였으나, 실제 Context Precision/Recall 기준으로는 **16개 중 꼴찌**로 확인됨 — proxy 지표가 잘못된 신호를 준 사례. 반면 w_bm25=0.5/w_vec=0.5("coverage형")는 proxy·실측 양쪽에서 모두 상위권으로 일관되게 확인됨.

다행히 5단계(실제 생성+RAGAS 전체 평가)에서 coverage형은 이미 검증해뒀고, 그 결과가 그대로 최종 채택 근거가 된다. ranking형 결과는 애초에 약한 후보를 잘못 골라 나온 것으로 설명됨.

- RRF(anchor) 대비 정규화 융합(coverage형)의 HM은 **+0.8~1.1%p 개선**
- 다만 anchor 대비 통계적 유의성 검정(28개 검정)에서는 유의 0건 — 129건 표본에서 "확실한 승자"라기보다 "방향성 있게 개선되고 손해는 없는 선택"

---

## 비용-이득 요약

| 설정 증가 | 얻는 것 | 발생하는 비용 |
|---|---|---|
| Candidate pool 증가 (50→100) | Recall/Coverage 소폭 증가, 100 이후 정체 | Reranking 대상 증가 |
| Reranker pool 증가 (20→50) | 검색 품질 개선 | latency 소폭 증가 |
| Reranker pool 증가 (50→100) | 개선 없음 | latency만 증가 |
| Top-k 증가 | Evidence Coverage 증가 | Context Precision 저하 가능·토큰 증가 |

---

## 종합

> **검색 후보를 충분히 확보하되(pool 100~150, rerank 50), 추가 후보의 이득이 줄어드는 지점에서 제한했다. 결합 방식은 정규화 융합(w_bm25=0.5/w_vec=0.5)이 실제 지표로도 확인된 최종 채택안.**
