# Goldset 확장 작업 인수인계 (Codex 앞)

이 폴더는 기존 13개 goldset(`scripts/agent_flow_eval/dataset/`)만으로는 v1/v2/v3 에이전트
평가가 통계적으로 빈약하다는 문제의식에서 시작한 **골드셋 확장** 작업의 전체 산출물이다.
470개 Bot Store 전체를 감사해서 "평가용 정답 데이터로 쓸 가치가 있는 워크플로우"를
단계적으로 걸러냈고, 마지막 단계는 사람(=이 작업을 하던 사용자)이 GPT에게 후보를
넘겨 Main/Challenge/제외로 분류받는 중이다. **아직 최종 확정되지 않았다** — 다음 라운드
진행, Main 세트 다양성 검토, Challenge 세트를 실제로 평가기에 어떻게 반영할지가 전부
열려 있는 상태로 인계한다.

## 왜 이 작업을 시작했나

기존 13개 goldset은 대부분 Excel/이메일/날짜계산류로 편중되어 있고, v1/v2/v3 비교의
통계적 근거로 삼기엔 표본이 너무 작다. 또 골드셋을 뭘 기준으로 넣고 뺄지에 대한 원칙이
그동안 문서화되어 있지 않았다(아래 "폐기된 개념" 참고). 그래서:
1. Bot Store에서 받은 470개 zip 전체를 감사 대상으로 삼고
2. 규칙 기반 필터로 명백히 가치 낮은 것들을 먼저 걸러내고
3. 로컬 LLM(Exaone)으로 1차 스크리닝하고
4. 사람이 재확인 가능한 명시적 원칙(3원칙, 아래 참고)으로 후순위 항목을 걸러내고
5. 최종적으로 GPT에게 남은 후보를 Main(바로 쓸 수 있음)/Challenge(평가기 확장 필요)/
   제외(가치 낮음)로 분류받는

파이프라인을 만들었다.

## 전체 파이프라인 (지금까지)

```
470개 zip 전체 unpack (unpack_all_downloads.py)
  -> normalize_manifests + extract_workflows 470개 전체 실행 (run_full_470_normalize_extract.py)
  -> RAG 카탈로그 완전일치 + main workflow + 액션수>=3 필터                    148개
  -> Exaone(EXAONE-4.0-32B, reasoning ON) "골드셋 가치 있음" 판정               49개 ("있음")
                                                                              90개 ("없음")
                                                                               9개 ("애매함")
  -> 49개에 3원칙 후순위 필터 적용 (exact package.action 매칭)
       Main 후보                                                              34개
       후순위 보존(DLL/Excel_MS.RunMacro/REST)                                15개
  -> 34개를 GPT에게 1차 전달 -> GPT 분류 (라운드 1)
       Main 10 / Challenge 9 / 제외 15
  -> [사용자가 라운드1 Main-10이 Excel 편중이라고 지적]
  -> 34개에 없던 나머지 풀(후순위 15개 + Exaone "없음/애매함" 99개 = 114개)을
     추가로 GPT에게 전달 -> GPT 누적 재분류 (라운드 2, 지금 여기)
       Main 18 / Challenge 21 / 제외 41 / (아직 GPT가 안 본 나머지 68개)
```

## 지금 폴더 구조

```
goldset_expansion/
  HANDOFF.md                              <- 이 문서
  scripts/                                <- 재실행 가능한 포터블 스크립트 (경로 하드코딩 없음)
    path_utils.py                         워크스페이스 루트 자동탐색 (processing/의 default_workspace_root()와 동일 규칙)
    exaone_goldset_judge.py               148개 후보 -> Exaone reasoning ON/OFF 판정 (로컬 llama.cpp 서버 필요)
    build_gpt_handoff_bundle.py           판정결과("있음" 49개) -> 3원칙 필터 -> Main 34 / 후순위 15
    organize_exaone_rejected.py           판정결과("없음"+"애매함" 99개) -> 별도 풀로 저장
    organize_cumulative_classification.py GPT가 준 분류 xlsx -> Main/Challenge/제외/미분류 폴더 재정리 (다음 라운드도 이 스크립트 재사용)
  candidate_pool/
    goldset_candidates_148.json           1차 규칙기반 필터 통과한 148개 원본 후보 목록
    goldset_candidates_no_dll.json        (중간 산출물) DLL 제외 버전
    tier1_clean_candidates.json           (중간 산출물) 9개 파일럿용
    gpt_bundle/
      candidates_main_34/                 라운드1 Main 후보 34개 원본 goldset.json
      candidates_deprioritized_15/        3원칙 후순위 15개 (DLL 5 / Excel_MS.RunMacro 4 / REST 8)
      candidates_exaone_rejected_99/      Exaone이 "있음"으로도 안 뽑은 나머지 99개
      exaone_judge_logs/                  위 49개("있음")의 Exaone 프롬프트+응답 전체 로그
      summary.md                          라운드1 선정 과정 요약
      workflow_goldset_screening_round1_34_final.md   GPT가 준 라운드1 결과 원문 (Main10/Challenge9/제외15)
      gpt_final_classification/
        1_Main_18/  2_Challenge_21/  3_제외_41/  4_미분류_잔여후보/   <- 라운드2 최종 결과
        workflow_goldset_screening_cumulative_80.xlsx  <- GPT가 준 라운드2 결과 원문 (전체 판정 시트에 사유 다 있음)
        README.md   <- ID 충돌 매칭 시 주의사항 (아래 요약)
  exaone_judge_logs_148/                  148개 전체(있음/없음/애매함 다 포함)의 Exaone 프롬프트+응답 원본 로그 + _all_results.json
```

## 핵심 방법론 / 원칙 (반드시 지켜야 함)

### 3원칙 후순위 필터 (34개 vs 15개를 가른 기준)
1. 액션명만으로 일반적인 규칙기반 채점이 어려운가
2. 정답 여부를 판단하려면 스크립트/함수명/URL/Body/SQL 등 하위 구현까지 확인해야 하는가
3. 그 구현 방식이 업무상 반드시 필요한 선택이 아니라 다른 A360 액션으로 대체 가능한가

후순위 = DLL, Python, JavaScript, VBScript, Custom Action/Package, Excel `RunMacro`,
`Application.runApp`, Stored Procedure, REST/SOAP, Execute SQL, 그리고 TaskBot.runTask는
**하위 워크플로우 해석에 실패했을 때만**(단순히 등장하는 것만으로는 안 됨 -
`resolve_subtask_coverage.resolve_transitive()`의 `unresolved` 반환값을 봐야 함).
File/Folder/MessageBox/Screen/LogToFile/XML/JSONHandler/Dictionary/List는 **이 필터에
포함되지 않음** (기능이 명확해서 별도 가치판단 대상) — 반드시 **정확한 package.action
일치**만 걸러야 하고, 패키지명 substring 매칭 같은 건 절대 하면 안 됨.

### 폐기된 개념 (다시 쓰지 말 것)
`scripts/agent_flow_eval/evaluation/core_task.py`의 `CORE_PACKAGE_KEYS`/`NON_CORE_PACKAGE_KEYS`와
`run_eval_case.py`의 `salient_families()`/`package_family()`의 "support" 세트는 File/Folder/
XML/JSONHandler/Dictionary/List를 놓고 **서로 모순**된다(둘 다 아무 근거 문서 없이 각자
만들어짐 - git log로 확인함). 골드셋 선별 작업에서는 "핵심업무계열", "애매패키지" 같은
이 옛 개념들을 전부 폐기하고 위의 3원칙으로 대체했다. **이 개념들을 다시 끌어와서 선별
기준으로 쓰지 말 것** — 실제로 이전 라운드에서 이걸 다시 쓰다가 사용자에게 지적받고
전부 폐기, 재계산한 적이 있다.

### ID 매칭 시 주의 (버그 이력 있음)
GPT 분류 xlsx의 4자리 ID는 **봇 폴더 단위 번호**라 유일하지 않다 - 같은 봇 폴더 밑에
서브 워크플로우 파일이 여러 개면 같은 ID를 공유한다. ID만으로 파일을 찾으면 안 되고
candidate 이름(정규화: 소문자+영숫자만)으로 파일명 꼬리와 대조해 구분해야 한다.
`organize_cumulative_classification.py`가 이 로직을 이미 구현하고 있다. 이름으로도
못 가르는 경우(예: `0203 API User Management Bot` - 같은 봇 밑에 `APIMaster.goldset.json`
15-step 전체 흐름과 `DeleteUserID.goldset.json` 서브태스크 두 파일이 있었음)는 스크립트가
"매칭 실패"로만 보고하고, 사람이 xlsx의 판정 근거 문장을 읽어서 어느 파일을 가리키는지
수동으로 확인해야 한다.

## 아직 안 끝난 것 / Codex가 다음에 할 일

1. **다양성 문제**: 라운드1 Main-10이 Excel 워크플로우로 편중되어 있다는 지적이 있었음.
   라운드2에서 Main이 18개로 늘었지만 (Outlook/폴더/날짜계산 등 비-Excel 항목 추가됨)
   여전히 Excel 비중이 높은지 확인 필요. 필요하면 `4_미분류_잔여후보/`(68개, 아직 GPT가
   안 본 candidates_deprioritized_15 + candidates_exaone_rejected_99의 나머지)에서 추가로
   골라 GPT에게 3차 라운드로 넘기는 것도 방법.
2. **Challenge-21 처리 방향 미정**: GPT는 이 21개가 "파라미터/외부 규칙/UI 객체까지 봐야
   공정하게 채점 가능"하다고 판단함 (serverType, HTML bodyFormat, DMN 규칙, Recorder UI
   객체, 정규식 등). 실제로 평가기(`run_eval_case.py`, `core_task.py`)를 확장해서 이 레벨까지
   채점할지, 아니면 이번 확장에서는 제외하고 나중 과제로 미룰지 결정 필요.
3. **Main-18 최종 확정**: GPT 분류는 1차 스크리닝일 뿐, 사람의 최종 수동 검토가 아직
   남아있음 (`요약` 시트에 "최종 수동 검토 후 골드셋 편입"이라고 명시되어 있음).
   최종 확정되면 `scripts/agent_flow_eval/dataset/`의 기존 13개 goldset과 같은 형식으로
   편입해야 함.
4. **다음 라운드를 돌릴 때**: GPT가 새 xlsx(예: `..._cumulative_120.xlsx`)를 주면
   `organize_cumulative_classification.py --xlsx <새 xlsx 경로>`를 그대로 재실행하면 된다
   (풀 소스는 `candidates_main_34/` + `candidates_deprioritized_15/` +
   `candidates_exaone_rejected_99/` 전체를 대상으로 함).

## 이 세션에서 병행되던, 이 번들과 직접 연결되지는 않지만 참고할 다른 이슈들
- v1 에이전트의 실제 2-turn 배치 채점이 비용 문제로 4/13에서 중단된 상태 (재개 지시 없었음).
- RAG 검색 파이프라인 자체가 병목이라는 진단(실제 쿼리 recall 30% vs oracle 96%)이 있었고,
  실험적 pushdown 필터 코드가 있으나 **로컬 전용, 절대 push 안 함** (Backend-eval-clean
  워크트리에만 존재).
- GitHub/Jira에서 `tmdals1207`이 만든 이슈와 거의 동시에 같은 이슈가 중복 생성된 사고
  (RPA-316 #427/#428, RPA-310 #417/#418) - 원인은 Jira->GitHub 미러링 자동화 두 개가 경합한
  것으로 추정되며, 아직 중복 이슈를 닫지 않은 상태(사용자 확인 대기 중).
- 평가 방법론 PPT 준비 문서: `A360-Session-Notes/10-workflow-eval-ppt-prep.md`.
