# 부록. char vs token, 그리고 상세 자료 위치

## 부록 A. char 기준과 token 기준, 실제 성능 차이 없음

동일한 실제 청크 크기라면(예: cs1200↔tok900, cs1500↔tok1024) 검색 성능 차이가 크지 않았다.

- paired 유의성검정(Wilcoxon+bootstrap) 25개(vector-only) + 59개(hybrid+rerank) + McNemar 31개 = **총 115개 검정, 유의 0건**
- char 기준 선택이 token 기준 대비 성능을 희생하지 않았다는 검증 자료

**의미**: char로 세든 token으로 세든 결국 같은 지점에서 자르는 것 — 이 코퍼스는 글자당 토큰 비율이 거의 일정(1.4자/토큰)해서 실제로 자르는 위치가 거의 같기 때문. "어떻게 셌나"가 아니라 "실제로 얼마나 잘렸나"만 성능에 영향을 준다.

---

## 부록 B. token 1000 vs 1024(2ⁿ) 차이 없음

12(또는 24) 토큰 차이는 실제 문서 분할과 검색 결과에 유의미한 영향을 주지 않았다. 1024가 "딱 떨어지는 숫자"인 건 컴퓨터(메모리·GPU 배치처리) 입장이지 텍스트 내용과는 무관 — 2ⁿ이 좋다는 건 하드웨어 얘기지 답변 품질 얘기가 아니었다.

*(참고: tok500/tok600은 실제로는 빈 테이블로 확인됨 — "600 대신 500을 썼다"가 아니라 둘 다 데이터가 없어서 tok512로 대체)*

---

## 상세 자료 위치 (같은 docs 폴더 내)

| 내용 | 위치 |
|---|---|
| EDA 전체 그래프(15개) | `EDA_그래프/` |
| EDA 핵심인사이트/테이블 원본 | `GPT_PPT_1_EDA_핵심인사이트_2026-07-27.md`, `GPT_PPT_2_EDA_테이블_2026-07-27.md` |
| overlap 심화 분석 원본 | `GPT_PPT_3_overlap_심화_2026-07-27.md` |
| hybrid 하이퍼파라미터 탐색 전체 결과 | `hybrid_hparam_search_full_2026-07-26.xlsx` |
| 유의성검정 원본(115개) | `paired_significance_test_2026-07-25.xlsx`, `hybrid_rerank_paired_significance_2026-07-26.csv`, `mcnemar_hitk_2026-07-26.csv` |
| gpt-4o-mini 단계별 검색방식 비교 원본 | `docs/local/gpt4o_mini_ragas_experiments_2026-07-25/gpt_handoff_2026-07-20/RAGAS_FINAL_RETRIEVAL_MODE_COMPARISON_2026-07-21.xlsx` |

---

## 평가 조건 (모든 페이지 공통)

```text
평가 데이터: 문서 4,680건 · Gold 질문 129건(status=approved, dataset_membership=active)
고정 조건: 동일 임베딩 모델(text-embedding-3-small) · 동일 평가셋
주요 지표: Context Precision / Recall · Hit@K · MRR · Evidence Coverage · Faithfulness · Answer Correctness
생성 모델: gpt-4o-mini(초기 스크리닝) 또는 로컬 EXAONE-4.0(이후 전 단계, reasoning off) — 페이지별로 명시
```
