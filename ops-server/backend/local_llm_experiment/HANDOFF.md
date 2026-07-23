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

## 6. 지금까지 나온 RAGAS 결과 (n=129 EXAONE-4.0, 생성+채점 둘 다 reasoning off)

| 지표 | tok128 | tok256 | tok300 |
|---|---|---|---|
| faithfulness | 0.758 | 0.831(n=128) | 0.784(n=128) |
| answer_relevancy | 0.911 | 0.901 | 0.922 |
| context_precision | 0.706 | 0.714 | 0.678 |
| context_recall | 0.761 | 0.809 | 0.769 |
| answer_correctness | 0.590 | 0.599(n=128) | 0.608(n=128) |
| hit@1 | 0.419 | 0.388 | 0.395 |
| hit@3 | 0.651 | 0.636 | 0.558 |
| hit@5 | 0.705 | 0.674 | 0.682 |
| reciprocal_rank | 0.533 | 0.503 | 0.498 |
| evidence_coverage | 0.352 | 0.534 | 0.487 |

256 vs 300 비교: 256이 대체로 근소 우위(faithfulness/context_precision/context_recall/
hit@3/evidence_coverage) — 그런데 300이 256보다 17% 더 커서 "2ⁿ이라서"인지 그냥 크기
차이인지 순수하게 못 가림. **진짜 결론은 250 vs 256, 1000 vs 1024(둘 다 2.3% 차이)로
내야 함 — 아직 미실행.**

## 7. 다음 세션 체크리스트

1. 서버(`192.168.1.147:8820`) 접속 확인, 모델이 `EXAONE-4.0-32B`인지 확인(4.5로 바뀌어
   있으면 안 됨 — 4.0으로 확정됐음).
2. RAGAS 평가 남은 후보(중요도 순 아님, 사용자와 순서 상의): **tok150, tok250, tok512,
   tok900, tok1000, tok1024, tok1200, tok1500, tok2048** (9개) — 위 3번 명령 그대로 반복.
   케이스당 약 1분(채점), 129건이면 배치 13개, 순조로우면 총 2~2.5시간/후보.
3. 250 vs 256, 1000 vs 1024, 128 vs 150 나오면 "2ⁿ이 더 좋다"는 통념 반박/지지 여부 정리.
4. **(2026-07-24 사용자 확정) 9개 tok 후보 다 끝나면 이어서 char 기준 14개 테이블도
   같은 조건(EXAONE-4.0, 생성+채점 로컬, reasoning OFF, 129건 전체)으로 RAGAS 평가.**
   테이블: `rag_documents_eval_cs300_ov0/ov30/ov60`, `cs600_ov0`(ov60은 빈 테이블,
   제외), `cs900_ov0/ov90/ov180`, `cs1200_ov0/ov120/ov240`, `cs1500_ov0/ov150/ov300`
   = 13개(cs600_ov60 제외). 명령은 3번과 동일 패턴:
   `--table-name rag_documents_eval_cs{N}_ov{M} --agent-label cs{N}_ov{M}_exaone40_full129
   --log-dir data/final_experiments/cs{N}_ov{M}`. gpt-4o-mini 결과(2026-07-20 산출,
   `docs/local/gpt_handoff_2026-07-20/RAGAS_CHUNK_SIZE_ALL_RESULTS_2026-07-20.xlsx`)와
   비교용 — 이 재실행이 끝나야 char vs tok, gpt-4o-mini vs 로컬을 한 기준으로 비교 가능.
5. tok600은 여전히 제외 상태 유지(재개하지 말 것, 사용자 판단).
6. 이 문서를 계속 갱신할 것 — 특히 6번 결과표와 7번 체크리스트.
