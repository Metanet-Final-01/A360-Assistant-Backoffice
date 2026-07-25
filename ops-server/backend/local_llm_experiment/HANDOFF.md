# 세션 인수인계 (작성: 2026-07-24, 이전 HANDOFF.md 전면 갱신 — 아래 내용이 최신)

이 문서는 `wip/local-llm-test` 브랜치, 커밋 `cda9407`(2026-07-23 push됨) 기준.
다음 세션은 결과보다 이 문서, 특히 "0번 경고"와 "5번 알려진 문제"를 먼저 읽을 것.

## 0. 가장 먼저 볼 것 — 브랜치 상태 경고 (여전히 미해결)

이 브랜치는 `codex/latest-ops-dev`(Codex가 작업 중인 최신 브랜치)와 **커밋 기준으로 갈라져
있음**(정확한 개수는 이 문서 작성 시점 기준 재확인 필요 — 예전엔 57개였음). 즉 이 브랜치는
최신 backend 코드 기준이 아니라 오래된 스냅샷 위에 로컬 LLM 실험을 얹은 것. 배포/PR용으로
쓰려면 사용자에게 먼저 물어봐서 (a) codex 위로 리베이스하거나 (b) 실험 관련 파일만 골라
옮기는 방법을 정해야 함 — **자동으로 리베이스/머지 시도하지 말 것.**

원격 저장소 이름이 `A360-Assistant-Backoffice`로 바뀌었음(github.com/Metanet-Final-01/
a360-assistant-backoffice) — 로컬 폴더명(A360-Assistant-Ops)과 다르니 헷갈리지 말 것.

## 1. 지금 하고 있는 실험이 뭔지 (한 줄 요약)

RAG 파이프라인의 chunk_size를 정하기 위해, **토큰 기준 청킹 후보 12개**(tiktoken)를
DB에 만들어두고, 각각을 **EXAONE-4.0-32B(로컬, 생성+채점 둘 다, reasoning 항상 OFF)로
승인된 골드셋 129건 전체 RAGAS 평가**하는 중. 목적: (a) chunk_size 자체를 정하는 것,
(b) 로컬 LLM을 gpt-4o-mini 대신 생성+채점에 써도 되는지(비용 $0) 검증하는 것 — 이 둘이
같이 진행되는 실험임.

## 2. 확정된 설정 (이 값들 임의로 바꾸지 말 것 — 전부 실측 근거로 정해짐)

- **생성 모델**: EXAONE-4.0-32B (4.5 아님 — 4.5는 4.0보다 느리고 재시도도 더 나서
  기각됨, 실측 비교 기록은 아래 6번 "지금까지 결과" 위 세션 기록 참고)
- **채점 모델**: EXAONE-4.0-32B, **같은 모델**(self-judge 설계 — 공정성 이슈는 사용자가
  인지하고 진행 중, GPT에 검증 맡기는 게 별도 계획으로 있었음)
- **reasoning**: 생성·채점 **둘 다 항상 OFF**. 서버 기본값이 모델마다 달라서(4.0=기본
  꺼짐, 4.5=기본 켜짐) 코드에서 명시적으로 통일함(`--local-gen-reasoning`로 켤 수 있지만
  기본은 끔).
- **RAGAS 프롬프트**: Faithfulness/ContextPrecision/ContextRecall/AnswerCorrectness는
  **RAGAS 기본 프롬프트 그대로**. AnswerRelevancy만 예외로 커스텀
  (`custom_ragas_prompts.py`의 `LanguageMatchedResponseRelevancePrompt`) —
  역생성 질문이 원문 언어와 다르게 나오는 문제(EXAONE 30/30건 영어)만 고침, strictness는
  RAGAS 기본값 3 그대로. Faithfulness 양태왜곡(가능→필수로 바뀌는 문제)은 커스텀
  프롬프트로 고치려다 EXAONE이 지시를 못 따라가서 **철회함** — RAGAS 기본으로 되돌림,
  두 judge(로컬/gpt-4o-mini) 다 같은 결함을 안고 있어 상대비교는 가능하다고 보고 진행.
- **judge max_tokens**: 캡 없음(제거함). RunConfig: `timeout=420, max_retries=8,
  max_workers=1`(단일 GPU 서버라 max_workers=2였을 때 동시요청 충돌로 타임아웃 연쇄나던
  문제 실측 확인 후 1로 고정 — 이거 절대 2 이상으로 올리지 말 것).
- **골드셋**: `app/eval/ragas_eval/cases/rag_goldset_v1.json`, **승인+활성 129건 전체**
  사용. `--max-cases`로 임의로 자르지 말 것(이전 세션이 계속 10건으로 잘라서 썼던 실수
  있었음 — 실제로는 129건 다 있었음). 분류: doc_page 68 + action_schema-jar 31 +
  action_schema-llm_agent 30 = 129.

## 3. 실행 방법

```
cd ops-server/backend
PYTHONUTF8=1 ../.venv/Scripts/python.exe -m local_llm_experiment.run_local_model_combo \
  --table-name rag_documents_eval_tok{N}_ov0 \
  --agent-label tok{N}_exaone40_full129 \
  --generator local --judge local-no-reasoning \
  --local-model EXAONE-4.0-32B \
  --log-dir data/final_experiments/tok{N}_ov0
```
- 서버(`http://192.168.1.147:8820/v1`) 접속 가능한지 먼저 `curl .../v1/models`로 확인
  (한 번에 모델 1개만 서빙됨, 지금 EXAONE-4.0-32B로 맞춰져 있어야 함).
- **이어하기(resume) 로직이 기본 켜짐** — 이미 5개 지표(faithfulness/answer_relevancy/
  context_precision/context_recall/answer_correctness) 다 채점된 case_id는 자동 스킵함.
  네트워크 끊겨서 죽어도 그냥 같은 명령 다시 실행하면 됨(`--no-resume`로 끌 수 있음).
- 결과: 공용 `data/eval_runs.jsonl`(agent_label로 구분, 다른 코드도 참조하니 안 옮김)
  + `--log-dir`에 이번 실행 전용 사본(`eval_runs_{agent_label}.jsonl`,
  `judge_raw_{agent_label}.jsonl`).

## 4. DB 후보 빌드 상태 — 전부 완료

토큰 기준(`rag_documents_eval_tok{N}_ov0`), overlap=0, 로컬 Postgres(port 5433):

| N | 건수 | | N | 건수 |
|---|---|---|---|---|
| 128 | 46,537 | | 900 | 9,346 |
| 150 | 39,755 | | 1000 | 8,688 |
| 250 | 24,805 | | 1024 | 8,539 |
| 256 | 24,326 | | 1200 | 7,647 |
| 300 | 21,300 | | 1500 | 6,710 |
| 512 | 14,025 | | 2048 | 5,768 |

**600은 의도적으로 제외**(사용자 판단) — `rag_documents_eval_tok600_ov0` 테이블은 존재하나
0건(빈 테이블, 무해하게 방치 중, 지워도 됨).

250/1000은 2^n(256/1024)과의 타이트한 비교(크기 차이 2.3%)를 위해 추가로 만든 것 —
128 vs 150, 256 vs 300 같은 느슨한 비교보다 "정확히 2ⁿ인가"만 순수하게 갈라볼 수 있음.
아직 이 비교(250 vs 256, 1000 vs 1024, 그리고 128 vs 150)는 RAGAS 평가가 남아서 실행 못 함.

char 기준(`rag_documents_eval_cs{N}_ov{M}`)은 이 세션 이전부터 있던 것 — cs300/600/900/
1200/1500 각각 여러 overlap 조합. gpt-4o-mini로 129건 돌린 결과가
`docs/local/gpt_handoff_2026-07-20/RAGAS_CHUNK_SIZE_ALL_RESULTS_2026-07-20.xlsx`에 있음
(doc_page는 cs300 압도적으로 좋음, action_schema는 cs1200~1500이 좋음 — 단일 chunk_size로
둘 다 못 만족한다는 결론). **이 char 기준 그리드도 로컬(EXAONE-4.0)로 다시 돌려야 함**
(사용자가 명시적으로 요청함, "gpt-4o-mini 기준으로 한 거라 로컬로 전부 다시") — 아직
시작 안 함.

## 5. 알려진 문제 (재발하면 이걸로 진단할 것)

1. **`max_workers=1` 필수** — 2였을 때 배치 하나가 TimeoutError 연쇄로 50분+ 걸리고
   케이스 실패까지 났음(2026-07-23, tok128 실행 중 실측). 원인은 순수 네트워크/서버
   과부하였고 토큰 한도 문제 아니었음(실패 케이스 답변 길이 확인함, 오히려 짧았음).
2. **EXAONE 결정론적 JSON 이스케이프 버그** — 답변에 `**"텍스트"**`처럼 볼드+따옴표
   중첩 마크다운이 있으면 Faithfulness의 statement 분해가 JSON을 깨뜨림. temperature=0라
   **재시도해도 100% 같은 방식으로 계속 실패함**(재시도 무의미). tok256/tok300에서 각각
   1건씩 발생(`rag_90a68094_5a57ed`, `rag_9b9b42c8_9d7fdc`) — **N/A로 두고 진행하기로
   사용자 확정함**, judge_raw 로그에 원인 다 남아있음. 129건 중 1건 정도 비율로 계속
   나올 수 있음, 정상 현상으로 간주.
3. **config.generator_model/evaluator_model 메타데이터 버그 — 수정 완료됨.** 예전 레코드
   중 이 커밋 이전 것(`cs1200_local_full_usefulness_test` 등, `data/eval_runs.jsonl`에
   남아있음)은 config에 실제 모델과 무관하게 "gpt-4o-mini"라고 잘못 적혀 있음 — 그
   레코드들 다시 볼 일 있으면 이 문서/대화 기록으로만 실제 조건 확인 가능.
4. **표본 10건은 전체 129건을 대표 못 함** — tok128을 n=10으로 먼저 돌렸을 때
   hit@1=0.6이었는데 n=129 전체는 0.419로 나옴. 급하게 결론 내지 말고 항상 129건
   전체 기준으로 판단할 것.
5. **DB 덤프**: `docs/local/db_dumps/rag_documents_eval_all_2026-07-23_v2.dump`(2.36GB,
   cs*+tok* 테이블 전부) — git으로 안 옮겨짐, 사용자가 구글드라이브로 직접 업/다운로드
   예정. 복원: `docker cp <dump> a360-postgres:/tmp/x.dump && docker exec a360-postgres
   pg_restore -U a360_admin -d a360 --no-owner /tmp/x.dump`

## 6. RAGAS 결과 — 전부 완료 (2026-07-25, n=129 EXAONE-4.0, 생성+채점 둘 다 reasoning off)

**계획했던 tok 9개 + char(ov0) 5개, 전부 완료.** tok128/256/300은 이 문서 이전 버전에
이미 기록됨(아래 표에 병합). 총 14개 후보 전체 비교표:

| candidate | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
|---|---|---|---|---|---|
| tok128 | 0.758 | 0.911 | 0.706 | 0.761 | 0.590 |
| tok150 | 0.768 | 0.908 | 0.710 | 0.790 | 0.615 |
| tok250 | 0.810 | 0.886 | 0.708 | 0.842 | 0.591 |
| tok256 | 0.831(n=128) | 0.901 | 0.714 | 0.809 | 0.599(n=128) |
| tok300 | 0.784(n=128) | 0.922 | 0.678 | 0.769 | 0.608(n=128) |
| tok512 | 0.779 | 0.908 | 0.673 | 0.804 | 0.627 |
| tok900 | 0.847 | 0.920 | 0.737 | 0.853 | 0.626 |
| tok1000 | 0.825 | 0.901 | 0.713 | 0.872 | 0.635 |
| tok1024 | 0.838 | 0.914 | 0.736 | 0.864 | 0.629 |
| tok1200 | 0.850 | 0.903 | 0.731 | 0.837 | 0.615 |
| tok1500 | 0.861 | 0.916 | 0.739 | 0.845 | 0.628 |
| tok2048 | 0.860 | 0.916 | 0.744 | 0.853 | 0.626 |
| cs300_ov0 | 0.792 | 0.900 | 0.722 | 0.817 | 0.599 |
| cs600_ov0 | 0.768 | 0.910 | 0.652 | 0.788 | 0.579 |
| cs900_ov0 | 0.801 | 0.925 | 0.677 | 0.810 | 0.606 |
| cs1200_ov0 | 0.841 | 0.907 | 0.728 | 0.864 | 0.623 |
| cs1500_ov0 | 0.854 | 0.919 | 0.712 | 0.833 | 0.612 |

(hit@k/reciprocal_rank/evidence_coverage는 tok128/256/300만 기록돼 있음 — 나머지
후보는 `data/eval_runs.jsonl`에서 재계산 가능, 이 표엔 RAGAS 5대 지표만 정리.)

**2ⁿ 가설 검증 (사용자 요청 3쌍)**:
- 128 vs 150 → **150 승** (context_recall 0.790 vs 0.761, precision 거의 동률)
- 256 vs 300 → **256(2ⁿ) 승** (양쪽 다 우위, 근데 300이 17% 더 커서 순수 비교 아님)
- 1000 vs 1024 → **거의 무승부** (precision 1024 근소 우위, recall 1000 근소 우위)
→ 3쌍 중 1승1패1무 — **"2ⁿ이 특별히 좋다"는 근거 약함.** 크기 자체(특히 900 이상)가
2ⁿ 여부보다 지배적 변수로 보임.

**토큰 기준 전반적 경향**: context_recall은 크기에 비례해 뚜렷이 상승(128~300대
0.76~0.84 → 900 이상 0.85~0.87). context_precision은 상대적으로 평평(0.67~0.74)하고
900~2048 구간이 대체로 상위. **tok512가 유일하게 precision 저점(0.673)** — 우연인지
재현되는지 추가 검증 여지 있음. 종합적으로 900 이상 구간이 tok 후보 중 상위권.

**char vs tok 비교**: 같은 크기대(900/1200/1500)에서 tok가 cs보다 대체로 근소 우위
(예: tok900 recall 0.853 vs cs900_ov0 0.810, tok1200 recall 0.837 vs cs1200_ov0 0.864는
반대로 cs가 근소 우위) — 뚜렷한 승자 없이 후보별로 갈림. char300은 doc_page 위주 그리드
(2026-07-20 gpt-4o-mini 결과)에서 강세였던 크기라 gpt-4o-mini 결과와의 직접 비교가
다음 단계로 남음(아래 8번).

## 7. 진행 상황 — RAGAS 평가 완료, 다음은 비교/분석 단계

**(2026-07-25) 계획했던 14개 후보(tok 9개 + char ov0 5개) RAGAS 평가 전부 완료.** 위
6번 표가 최종 결과. 알려진 JSON 파싱 버그(2번 항목)로 케이스별 소수 N/A는 있으나 전체
실행은 14개 전부 정상 종료(각 129/129 저장, 에러로 죽은 후보 없음).

다음 세션 체크리스트:

1. 서버(`192.168.1.147:8820`) 접속 확인, 모델이 `EXAONE-4.0-32B`인지 확인(4.5로 바뀌어
   있으면 안 됨 — 4.0으로 확정됐음). **단, 추가 RAGAS 실행이 당장 예정돼 있진 않음** —
   아래 2~4번이 먼저(전부 분석/비교 작업, GPU 불필요).
2. **gpt-4o-mini 결과와 직접 비교**(아직 안 함) — `docs/local/gpt_handoff_2026-07-20/
   RAGAS_CHUNK_SIZE_ALL_RESULTS_2026-07-20.xlsx`(gpt-4o-mini, char 기준, 2026-07-20
   산출)를 6번 표(EXAONE-4.0, char+tok)와 나란히 놓고: (a) 로컬 모델이 gpt-4o-mini
   대비 생성+채점 품질을 어느 정도 유지하는지, (b) chunk_size 최적값이 judge 모델에
   따라 달라지는지 확인.
3. **250 vs 256, 1000 vs 1024, 128 vs 150 "2ⁿ 가설" 결론은 6번에 이미 정리됨** — 근거
   약함(1승1패1무)으로 결론. 재확인 필요하면 6번 참고.
4. tok512의 context_precision 저점(0.673, 전체 중 최저)이 우연인지 재현되는지 —
   원한다면 tok512만 --no-resume로 재실행해 재현성 확인 가능(케이스당 채점 seed가
   RAGAS 내부에서 고정 아니므로 완전히 같은 값은 안 나올 수 있음).
5. tok600은 여전히 제외 상태 유지(재개하지 말 것, 사용자 판단). overlap 변형 8개
   (cs300_ov30/60, cs900_ov90/180, cs1200_ov120/240, cs1500_ov150/300)는 DB엔 있지만
   아직 RAGAS 미평가 — 필요해지면 3번 명령 패턴 그대로 추가 가능.
6. 이 문서를 계속 갱신할 것 — 특히 6번 결과표.
