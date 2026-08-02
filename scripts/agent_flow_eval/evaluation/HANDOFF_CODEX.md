# 채점 방법론 재설계 — Codex 인수인계 (2026-07-30/31)

## 지금 브랜치/repo 상태부터 (중요, 작업 시작 전에 확인할 것)

- **저장소**: `A360-Assistant-Ops-rpa187` (a360-assistant-backoffice의 워크트리)
- **브랜치**: `feat/RPA-187-goldset-scripts` — **origin/dev보다 161커밋 뒤처짐**,
  로컬 커밋 2개 앞섬(예전 PR #126, `scripts/goldset` → `agent_flow_eval` 이름변경 +
  470개 감사 파이프라인). **아직 dev로 rebase/merge 안 함** - 이 브랜치를 오늘
  작업 그대로 커밋할지, dev로 먼저 맞출지는 사용자 확인 필요.
- **오늘 이 브랜치에 생긴 변경사항** (아직 커밋 안 됨, `git status` 그대로):
  ```
  M  scripts/agent_flow_eval/evaluation/README.md
  M  scripts/agent_flow_eval/evaluation/adapters/pm4py_adapter.py
  M  scripts/agent_flow_eval/evaluation/adapters/worfbench_adapter.py
  D  scripts/agent_flow_eval/evaluation/core_task.py
  M  scripts/agent_flow_eval/evaluation/run_eval_batch.py
  M  scripts/agent_flow_eval/evaluation/run_eval_case.py
  ?? scripts/agent_flow_eval/evaluation/action_chain.py
  ?? scripts/agent_flow_eval/evaluation/action_equivalence_rules_conditional.json
  ?? scripts/agent_flow_eval/evaluation/action_matching.py
  ?? scripts/agent_flow_eval/evaluation/critical_attribute/
  ?? scripts/agent_flow_eval/evaluation/gold_core_actions/
  ?? scripts/agent_flow_eval/evaluation/SCORING_REDESIGN_PLAN.md  (이 문서 옆에 있음)
  ?? scripts/agent_flow_eval/evaluation/HANDOFF_CODEX.md          (이 파일 자체)
  ?? scripts/agent_flow_eval/goldset_expansion/export_main_challenge/
  ?? scripts/agent_flow_eval/goldset_expansion/scripts/export_cleaned_variant.py
  ?? scripts/agent_flow_eval/goldset_expansion/scripts/export_main_challenge_bundle.py
  ```
- **이 세션과 무관한, 건드리면 안 되는 기존 dirty 파일들** (다른 진행 중인 작업,
  손대지 말 것):
  ```
  M  rag-server/app/rag/scripts/backfill_missing_embeddings.py
  M  rag-server/app/rag/scripts/reindex_opensearch_from_db.py
  M  rag-server/app/rag/store/opensearch_client.py
  ?? docs/Session_Node/
  ?? scripts/ragas_eval/
  ```
- **로컬 도커 상태**: `A360-Assistant-Backend-eval-clean`에서 테스트하려고
  `a360-backend`/`a360-postgres` 컨테이너를 새로 띄웠는데, 원래 `A360-Assistant-Backend`
  (dirty `local/observability-schema-experiment` 브랜치)가 쓰던 동일 이름
  컨테이너와 충돌해서 그것들을 `*-original-20260730`로 rename만 해뒀다(삭제 안 함,
  데이터 그대로). Codex가 로컬 도커를 만지게 되면 이 rename된 컨테이너들의
  존재를 알아둘 것.
- **`A360-Assistant-Backend-eval-clean`의 `.env`**: `LOCAL_RAG_DATABASE_URL`
  (기존 `RAG_DATABASE_URL`과 같은 값, docker-compose가 로컬 override를 우선하는
  버그성 동작 때문에 추가함), `DEBUG_ENDPOINTS_ENABLED=true`(액션 카탈로그
  검색 디버그 엔드포인트 테스트용) 두 줄이 로컬에 추가돼 있음. `.env`는
  gitignore돼서 이 저장소엔 안 올라감 - 다른 사람이 같은 테스트를 재현하려면
  똑같이 추가해야 함.

## 왜 이 작업을 했나

`scripts/agent_flow_eval/evaluation/run_eval_case.py`가 v1/v2/v3 에이전트
비교에 쓰던 채점(package.action multiset/LCS + PM4Py conformance + WorFBench
chain-F1)이, 사용자가 직접 만든 실제 A360 봇("금 시세 조회 후 결과 발송")을
정답 삼아 실제 에이전트 예측과 비교해본 결과 근본적으로 안 맞다는 게 실증됐다:
PM4Py는 "실행로그 vs 허용모델" 비교용인데 우리는 "사람이 고른 구현 1개 vs
에이전트가 고른 다른 구현"을 비교하고 있어서, 정상적인 구현 차이(Recorder vs
WebAutomation 등)까지 전부 deviation으로 잡혀 fitness 0.089/precision 0.0가
나왔다.

**설계 근거/전체 논의 과정은 `SCORING_REDESIGN_PLAN.md`(이 폴더에 같이 넣어둔
plan 파일, GPT 검토를 3~4차례 거치며 나온 최종 설계)를 반드시 먼저 읽을 것** -
왜 Kuhn's algorithm을 뺐는지, 왜 LCS 대신 LIS를 주 계산으로 쓰는지, 왜
Required Order 자동도출을 폐기했는지 등 세부 결정 이유가 다 있다.

## 새 구조 (한눈에)

```
1. Gold Core Action 고정 (gold_core_actions/<id>.json, uid별 include/exclude)
2. Rule Match (canonical label 완전일치 + 조건부 Action Equivalence, signature-aware 배정)
3. Judge Match (Rule로 안 잡힌 것만, 임베딩 상호 Top-1 + 최소유사도 사전필터 → gpt-4o-mini 최종판정)
4. Action P/R/F1 (1차 확정 지표)
5. Action Chain F1 (확정된 매칭 쌍으로 LIS, LCS-DP로 교차검증)
6. WorFEval Chain F1 (원본 벤더 라이브러리 그대로 - 외부 벤치마크 비교용 정보성 표시만)
```

- **`action_matching.py`**: 매칭 엔진 전체. `score_action_matching(gold_steps, pred_steps, gold_core_actions_path=None)`가 진입점.
- **`action_chain.py`**: `compute_action_chain(gold_actions, pred_actions, matches)`. LIS와 LCS가 다르면(매칭이 이미 1:1 확정이라 수학적으로 같아야 정상) `AssertionError` - 실제로 이걸로 uid 중복 버그를 하나 잡았음(§"발견한 버그" 참고).
- **`critical_attribute/`**: Recorder/WebAutomation처럼 다른 패키지끼리도 비교 가능한 `common_signature()`(operation/target_text 정규화), `compare_common()`, `judge_action_equivalence()`. `README.md`에 상세 설명 있음.
- **`action_equivalence_rules.json`**(기존, 안 건드림)과 별개로 **`action_equivalence_rules_conditional.json`**(신규) - attribute 조건부 동치 규칙(예: `Recorder.capture(operation=CLICK) ≡ WebAutomation.clickelement`). 실제 검증된 것만 등록, 추측성 대량생성 금지.
- **`gold_core_actions/gold_price_bot.json`**: 정답 워크플로우 중 "구현 기교"(수동 셀 서식 반복 19개)를 uid로 고정 제외. 다른 goldset에도 필요하면 같은 패턴으로 추가.
- **삭제**: `core_task.py`(근거 없는 하드코딩 CORE_PACKAGE_KEYS 분류, git log로 도입 커밋에 근거 설명 없음을 확인함), `run_eval_case.py`의 `package_family()`/`salient_families()`(마찬가지 이유). **PM4Py는 코드는 남기고 액티브 리포트에서만 뺐음**(`adapters/pm4py_adapter.py` 그대로 존재, `run_eval_case.py`가 안 부를 뿐).

## 검증 상태

1. **금 시세 조회 봇 케이스**: 사람이 직접 만든 기대표(정답: Excel_MS.CreateSpreadsheet vs 예측: OpenSpreadsheet(filePath=null)은 Unmatched가 맞다, Recorder.capture(CLICK)×2/EXTRACT_TABLE은 Rule Match가 맞다, Gmail 3종은 FN이 맞다 등)와 **100% 일치 확인함**.
2. **기존 13개 goldset 전체**: `run_eval_batch.py`로 회귀 테스트, 처음엔 `12_0338_lettergenerationbot`에서 LIS≠LCS assertion에 걸림 → 원인 추적해서 **실제 버그 발견**: 하위 워크플로우가 여러 지점에서 inline되면 같은 uid가 flatten된 목록에 중복으로 나타나서(예: `resolve_subtask_coverage.py`가 여러 호출지점에 같은 서브워크플로우를 복사해 넣음) LIS 계산이 잘못된 위치를 가리킴. **수정함**: `ScoredAction.uid`를 항상 `f"{원본uid}#{전역occurrence순번}"`로 유일하게 만듦(include_map 조회는 원본 uid 기준으로 따로 함). 수정 후 13/13 통과.
3. **v1/v2/v3 실제 실행 결과** (같은 PDF, 같은 모델 gpt-5.6-luna): v1 F1=0.091, v2 F1=0.0(액션 0개 생성 - 에이전트가 카탈로그 확신 부족으로 포기), v3 F1=0.242(노이즈 필터 추가 후). **사용자 기대 순서(v1<v2<v3)와 실제 결과(v2<v1<v3)가 안 맞음** - 아직 미해결.

## 오늘(2026-08-02) 4건 코드리뷰 피드백 반영 결과

사용자가 file:line을 짚어가며 준 4건 피드백, 전부 처리함:

1. **`action_matching.py`의 `JUDGE_CANDIDATE_MIN_SIMILARITY = 0.3`(임시값, 튜닝
   근거 약함)** → **제거함**. 상호 Top-1 필터("서로가 서로를 가장 가깝다고 본
   유일한 쌍")만으로 후보를 좁히고, 최종 same/different는 어차피 Judge가
   판단하므로 별도 유사도 컷오프 없이도 임의 배정 문제는 안 생긴다고 판단.
   `judge_log`에는 여전히 `similarity` 값을 기록하므로, 나중에 실제 분포를
   보고 근거 있는 값이 필요해지면 다시 넣을 수 있음.
2. **Gold Core Action이 배치 평가에 연결 안 됨** → **연결함**.
   `run_eval_batch.py`에 `gold_core_actions_path_for(case_id)` 추가 -
   `gold_core_actions/<case_id>.json`이 있으면 자동으로 `score_normalized()`에
   넘기고, 없으면 기존과 동일하게 `None`(전부 포함). 지금은 13개 goldset용
   파일이 아직 없어서 실질 동작 변화는 없지만, 케이스별 파일이 추가되면
   자동으로 배치에 반영된다.
3. **문서/코드 불일치**(`run_eval_case.py:25` 주석이 `core_task.py`를 "남겨뒀다"고
   잘못 기술, `README.md`가 여전히 옛 `core_task`/`core_pm4py`/`core_worfbench`/
   `salient_family`/`package_family`를 현재 지표처럼 설명) → **둘 다 고침**.
   `run_eval_case.py` 주석은 "파일째 완전히 삭제됨"으로 정정. `README.md`는
   "Current score layers" 표, `core_task_*` 설명 문단, "Rule Governance"의
   "Core-task rules" 절까지 전부 새 `action_matching.py`/`action_chain.py`/
   `gold_core_actions` 기준으로 다시 씀.
4. **`action_chain.py`의 `AssertionError`가 배치 전체를 죽일 위험** →
   **코드 변경 없이 이미 안전함을 확인**. `run_eval_batch.py`의 `main()`이
   `evaluate_case()` 호출을 케이스 단위 `try/except Exception`으로 감싸고
   있고, `AssertionError`도 `Exception`의 하위클래스라 그대로 잡힌다. 실제로
   앞서 13개 goldset 회귀에서 uid 중복 버그가 있던 케이스 1개가 정확히 이
   경로로 `status="error"` row만 남고 나머지 12개는 정상 완료된 걸 직접
   확인함. 다만 조용히 삼키는 게 아니라 raw하게 던지는 이유(매칭 로직 버그
   신호를 숨기지 않기 위함)를 `action_chain.py`에 주석으로 명시적으로
   남겨둠 - 배치 실행 결과가 이미 요구사항을 충족하지만, 다음 사람이 다시
   같은 질문을 하지 않도록.

## v2 실행별 비일관성 — 원인 확인 및 최소수정 (2026-08-02, 오늘 추가)

**증상**: 같은 PDF+모델로 v2를 여러 번 돌리면 한 번은 액션 8개(자신있게 생성),
한 번은 0개(카탈로그에서 확인 안 된다며 완전 포기)를 만드는 등 결과가 극단적으로
갈렸다.

**원인**: `app/agent/v2/recommend/graph.py`의 `_make_llm()`이 `temperature`를
지정하지 않고 있었다(공급자 기본값 그대로) - "이 업무는 카탈로그에서 확인 안
돼서 자동화 불가능하다"는 판단 자체가 매번 다른 샘플링 결과에 따라 흔들린
것으로 보인다.

**최소 수정** (`A360-Assistant-Backend-eval-clean/app/agent/v2/recommend/graph.py`
의 `_make_llm()`, 다른 팀 소유 저장소라 **로컬 eval-clean 체크아웃에만** 적용,
아직 커밋 안 함 - 사용자 확인 필요): `ChatOpenAI(...)`에 `temperature=0, seed=0`
추가. `orchestrator/jsonio.py`에 이미 있던 관례("같은 입력이면 같은 답이 나와야
하는 호출은 0을 준다")를 recommend 생성 경로에도 적용한 것.

**검증 결과** (같은 PDF, 수정 후 v2를 4번 반복 실행):
| 실행 | pred_count | Action F1 |
|---|---:|---:|
| temp0_a | 8 | 0.385 |
| temp0_b | 2 | 0.20 |
| temp0seed0_a | 6 | 0.25 |
| temp0seed0_b | 7 | 0.16 |

**결론(있는 그대로)**:
- **0개 액션으로 완전히 포기하는 케이스는 4번 다 사라짐** - 원래 증상이었던
  "가끔 통째로 0점" 문제는 고쳐졌다고 봐도 됨.
- 다만 **완전한 결정성은 안 됨** (OpenAI 쪽에서 `temperature=0`+`seed`도 "대체로
  결정적"이라고만 문서화 - 특히 여러 턴 tool-calling 루프에서는 각 스텝의 미세한
  샘플링 차이가 누적됨). F1이 여전히 0.16~0.385로 실행마다 갈림.
- **v1<v2<v3 순서는 확정적으로 재현되지 않았다.** 같은 조건으로 v1=0.091(1회),
  v3=0.242/0.229(2회, 비교적 안정적), v2=0.16~0.385(4회, 평균 약 0.25)를 실측한
  결과: **v1 < v2**, **v1 < v3**는 매 실행 확실히 성립하지만, **v2 vs v3는
  통계적으로 거의 동률**이고 v2가 v3를 넘는 실행도 있었다(temp0_a=0.385 >
  v3 두 실행 모두). 이건 채점 로직 버그가 아니라 v2 에이전트 자체의 실행별
  변동폭이 v1↔v3 격차보다 커서 생기는 실측 결과다 - 점수를 억지로 원하는
  순서에 맞추는 추가 수정은 하지 않았다(근거 없는 튜닝 금지 원칙).
- 순서 주장을 하려면 각 버전을 여러 번 돌려 평균±분산으로 비교해야 하고,
  v2는 특히 표본을 더 늘려야 한다는 게 이번에 실측으로 확인된 사실이다.

## 아직 안 끝난 것 / Codex가 볼 것

1. **v1<v2<v3 순서 검증에는 표본이 더 필요함**(위 §"v2 실행별 비일관성" 참고) -
   지금은 v1=1회, v3=2회, v2=4회 실측 기준. 특히 v2는 분산이 커서 최소
   10회 이상 반복해 평균±표준편차로 비교하는 게 맞아 보임 - 아직 안 함.
2. **Judge Match 자체의 비결정성**: 노이즈 필터 추가 전후로 v3의 Judge Match가 1건→0건으로 바뀜(CreateSpreadsheet≡office365ExcelCreateWorkbook 매칭이 사라짐) - 노이즈 제거로 "남은 후보 풀"이 바뀌어 상호 Top-1 계산이 달라졌거나, LLM 판정 자체가 흔들렸을 가능성. 원인 미확인.
3. **카탈로그 중복 색인 문제**(이전부터 있던 이슈, 미해결): "Microsoft 365 Excel package in Automation 360"(camelCase 액션ID) vs "Microsoft 365 Excel"(사람이 읽는 이름) 두 계열이 RAG 벡터/BM25 공간에서 거의 안 섞임 - v3 미매칭 다수가 이 문제.
4. **예측 쪽 Core Action 필터 비대칭**: `gold_core_actions`는 정답에만 적용됨(정답의 수동 서식 반복 19개 제외). 예측이 다른 방식으로 서식을 구현하면(office365ExcelCreateTable 등) 그건 그대로 FP로 남음 - GPT 검토가 지적한 비대칭 문제, 아직 해결 안 함(서식 관련 액션을 양쪽에서 어떻게 대칭적으로 다룰지 설계 필요).
5. **다대일 구현차이 패턴 매칭**: `DataTable.deleteRow×3/insertRow×2 ↔ Excel_MS.SetCellFormula` 같은 케이스는 의도적으로 미해결 상태(Unmatched)로 남겨둠 - 패턴 DSL을 새로 만들 필요가 실제로 반복되면 그때 추가하기로 함.

## 참고 - GPT 상담 자료 (외부 저장소, 이 repo 아님)

`A360-Session-Notes/업무정의서/금 시세 조회 봇/gpt_handoff/`(다른 워크스페이스
폴더, git 아님)에 GPT와 주고받은 전체 자료(SUMMARY.md, gold 원본/정규화본,
v1/v2/v3 예측 원본/정규화본/채점결과, 채점 코드 사본)가 정리돼 있음 - 이번
재설계의 실제 논의 맥락을 더 보고 싶으면 참고.
