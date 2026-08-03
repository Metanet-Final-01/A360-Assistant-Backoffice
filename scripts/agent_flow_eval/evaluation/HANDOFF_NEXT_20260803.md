# 핸드오프 문서 (다음 Claude 세션용, 2026-08-03 작성)

이 문서는 2026-08-03 세션에서 한 작업 전체를 다음 사람(다음 Claude Code 세션)이
빠르게 파악하고 이어서 작업할 수 있도록 정리한 것이다. `HANDOFF_CODEX.md`(더
오래된 2026-07-30/31 기록, Codex 인수인계용)와 역할이 겹치지 않게 이 세션에서
새로 생긴 것 위주로 썼다 - 배경 설계 이유는 여전히 `HANDOFF_CODEX.md`와
`SCORING_REDESIGN_PLAN.md`가 정본이다.

## 1. 먼저 읽을 것 (순서대로)

1. 이 문서
2. `evaluation/README.md` - 채점 아키텍처 전체(활성 지표가 뭔지, 뭐가
   deprecated됐는지)의 정본. 이번 세션에 핵심업무 분류/분기 진단/WorFBench
   9개 케이스 연결/PM4Py 삭제를 전부 반영해서 갱신함.
3. `evaluation/HANDOFF_CODEX.md` - 2026-07-30/31 기록. "2026-08-02 정책 정정"
   섹션이 맨 위에 있음(Gold Core Action UID 방식 폐기 이유) - 이번 세션에
   만든 핵심업무 분류(`classify_core_business_actions`)와 **다른 시스템**이니
   헷갈리지 말 것(§3 참고).
4. `goldset_expansion/confirmed_goldset/README.md` - 9개 확정 케이스가 뭔지,
   왜 이 9개인지.
5. `goldset_expansion/confirmed_goldset/evaluation_results/final_repro_v1_v2_v3_20260803.md` -
   이번 세션 최종 결과 보고서. **가장 중요한 산출물.**
6. `goldset_expansion/confirmed_goldset/evaluation_results/final_v1v2v3_20260803_detail.xlsx` -
   위 보고서의 세부 데이터. **첫 번째 시트("RPA 워크플로우 생성 평가 — 핵심
   지표")가 PPT에 바로 쓸 수 있는 요약**, 나머지 8개 시트가 세부 검증용.

## 2. 지금 상태 요약

- **`feat/RPA-187-goldset-scripts` 브랜치가 `dev`에 완전히 merge됐다**
  (2026-08-03, merge 커밋 `bc2729e`). PR #126은 GitHub가 자동으로 MERGED
  처리했다(dev가 feat의 모든 커밋을 포함하게 되면서 자동 감지됨). **이 브랜치는
  이제 별도 작업 브랜치로서의 의미가 없다** - 다음 작업은 `dev`에서 새 브랜치를
  따서 시작하면 된다.
- 9개 확정 goldset 케이스(0085/0089/0098/0112/0131/0140/0164/0376/0419)에
  대해 v1(1회)/v2(2회)/v3(2회) 실제 에이전트 재실행 + 채점을 완료했다. 전부
  같은 모델(`gpt-5.4-mini`, temperature=0/seed=0)로 통일해서 공정하게 비교함.
- **Rule-only Macro F1(재현 가능한 공식 기준선)**: v1=0.328(1회), v2=0.279
  (0.233~0.326, 2회 평균), v3=0.414(0.367~0.462, 2회 평균).
- **PM4Py는 이번 세션에 완전히 삭제됐다**(코드/의존성/문서/CI 참조 전부) -
  더 이상 "kept but unused"가 아니라 파일 자체가 없다. 되살리지 말 것.

## 3. 이번 세션에 새로 생긴 핵심 아키텍처 (자세히)

### 3-1. 실제 9개 케이스를 채점하는 스크립트는 `audit_final_goldset.py`다

**`run_eval_batch.py`가 아니다** - 그건 `eval_inputs/normalized_workflows_13/`
기준 레거시 13개 케이스 전용이고, 9개 확정 케이스와는 디렉터리 구조 자체가
다르다(`resolve_paths()`가 `normalized_workflows_13/<case_id>`를 찾는데, 9개
케이스 gold는 `goldset_expansion/export_main_challenge/deliverable/Main/`에
있다 - `MAIN_GOLD_DIR` 상수 참고). 이걸 헷갈려서 시간 많이 썼으니 다음 사람은
그러지 말 것.

`audit_final_goldset.py`의 CLI는 v2/v3만 받는다(`--v2-manifest`/
`--v3-manifest`). v1이나 추가 재현성 샘플을 채점하려면 `score_case(version,
case_id, run_id)`를 직접 호출하는 짧은 스크립트를 만들어야 한다(이번 세션에
`_score_v1_fresh.py`, `_rescore_prior_corebrief_run.py`를 그렇게 만들어서 쓰고
지웠다 - 재현하려면 §4의 RUN_IDS 딕셔너리 패턴 참고).

### 3-2. 핵심업무 분류 (`classify_core_business_actions`, `action_matching.py`)

Gold/예측 액션 중 패키지·액션명만으로는 핵심업무인지 범용 셋업(로그 폴더
준비, 경로 조립, 검증 메시지 등)인지 구별 안 되는 것만(`action_filters.
AMBIGUOUS_GENERIC_ACTIONS` 목록) 대상으로:
1. 키워드 규칙(`INFRASTRUCTURE_KEYWORD_RE` = log/audit/error/snapshot/
   observability) - 무료, 9개 케이스 실측으로 100% 정확 확인됨.
2. 남은 것만 gpt-4o-mini(`judge_core_business_relevance`, `critical_attribute/
   judge.py`)가 업무정의서 원문 + 실제 파라미터 값을 보고 판정.

**Gold/예측 양쪽에 대칭 적용**된다 - `HANDOFF_CODEX.md`가 폐기한 `gold_core_
actions/`(Gold 전용 비대칭 UID 목록)와는 **다른 시스템**이다. 이 구분을 반드시
알아야 한다 - `gold_core_actions/` 디렉터리는 여전히 존재하지만 사람이 검토한
과거 참고자료일 뿐, 이 분류 함수가 그 파일들을 읽거나 쓰지 않는다(다만
2026-08-03에 이 새 분류기 결과를 `gold_core_actions/0089.json`,
`0098.json`(사람 검토본)과 대조해서 52/52 일치를 확인했다 - 교차검증 근거로
남겨둠).

결과는 `gold_core_actions/_llm_classification_cache.json`에 케이스ID+라벨+
파라미터 키로 캐싱된다. **이 프롬프트를 고치면 캐시를 반드시 지워야 한다**
(§6 "함정" 참고) - 안 지우면 옛날 판정이 계속 재사용된다.

### 3-3. 분기(if/elseIf/else) 진단 지표 (`score_branch_coverage`)

상호배타적 분기(if/elseIf/else)가 flatten될 때 하나의 리스트로 다 풀려서
생기는 중복 카운트 문제(실측: `0376`의 3분기 if/elseIf/elseIf, 각 분기가
거의 동일한 copyFiles+deleteFiles를 반복)에 대한 **진단 전용 별도 지표**다.
**메인 Action P/R/F1에는 전혀 반영 안 된다** - 사용자가 명시적으로 "새 분기
매칭 알고리즘은 만들지 말고 이미 계산된 Rule/Judge 매칭 결과만 재사용하라"고
결정했기 때문에, 분기-대-분기 매칭 알고리즘 없이 단순 재집계만 한다.

- **Branch Coverage** = 완전매칭된 분기 수 / 전체 분기 수 (all-or-nothing,
  임의 임계값 없음)
- **Branch Score** = 분기별 매칭 비율의 평균 (부분점수 반영)

**아직 안 풀린 문제**: 이 지표는 진단만 하지 실제로 메인 점수의 중복 카운트
문제를 고치지는 않는다. 사용자가 "이건 별도 항목으로 남겨두겠다"고 명시적으로
미룬 사안이다 - 필요해지면 분기-대-분기 매칭(예: 분기 내용 유사도로 그리디
배정)을 추가로 설계해야 한다.

### 3-4. WorFBench 외부 벤치마크가 9개 케이스에도 처음 연결됨

기존엔 레거시 13개 케이스에만 있었다(`run_eval_case.py`). `audit_final_
goldset.py`의 `score_case()`에 `score_worfbench_f1chain()`을 새로 붙였다.
**Qodo 코드리뷰가 실제 버그를 잡았다** - 이 외부 라이브러리(`a360-eval-
sandbox/external/WorFBench` sibling 워크스페이스 + `sentence_transformers`
패키지)가 없는 환경에서 import가 실패하면 전체 감사가 죽는 문제였다. 지금은
`worfbench_adapter.py`의 `score_worfbench_f1chain()` 자체 try/except 안으로
import를 옮기고, 호출부(`audit_final_goldset.py`)에도 방어적 try/except를
추가해서 WorFBench가 없어도 Rule-only/Judge-assisted 주 지표는 정상 산출된다.

### 3-5. PM4Py 완전 삭제 (2026-08-03)

`adapters/pm4py_adapter.py`, `processing/convert_to_pm4py.py` 파일 자체를
삭제했다(이전엔 "액티브 리포트에서만 제외, 코드는 남김"이었는데 이번에 아예
없앰 - PM4Py를 쓰지 않기로 확정했고, 이게 dev merge 충돌 하나의 해결책도
됐다). `worfbench_adapter.py`가 쓰던 공유 라벨 정규화 유틸(`_canonical_
label`, `_split_action_label`, `load_action_equivalence_map`, 원래도 PM4Py와
무관한 단순 유틸이었음)은 `worfbench_adapter.py` 안으로 옮겼다. 참조 사이트
6개 파일도 다 정리했다(`plan_processing_pipeline.py`,
`build_eval_input_artifacts.py`, `convert_backend_recommendation.py`,
`export_main_challenge_bundle.py`, `export_cleaned_variant.py`,
`run_eval_case.py`의 `Paths`/`resolve_paths()`).

## 4. 재현성 비교를 다시 돌리려면 (정확한 명령어)

```bash
# 1) 에이전트 재실행 (버전별로, PDF는 이미 confirmed_goldset/pdfs/에 있음)
cd scripts/agent_flow_eval
python runner/run_runner_v2_batch.py \
  --pdf-dir goldset_expansion/confirmed_goldset/pdfs \
  --agent-version v2 \
  --run-prefix <run-prefix> --continue-on-error

# 2) 채점 (v2/v3는 CLI 지원, v1/추가 샘플은 score_case() 직접 호출 스크립트 필요)
cd evaluation
python audit_final_goldset.py \
  --v2-manifest ../runner/logs/<run-prefix-v2>/batch_manifest.json \
  --v3-manifest ../runner/logs/<run-prefix-v3>/batch_manifest.json \
  --output-dir ../goldset_expansion/reports/final_goldset_9_evaluation/<name>

# 3) 엑셀 리포트 생성 (헤드라인 시트 + 8개 세부 시트)
python export_audit_to_excel.py <output-dir>/audit.json \
  --output ../goldset_expansion/confirmed_goldset/evaluation_results/<name>.xlsx \
  --subtitle "원하는 부제목"
```

여러 재현성 샘플(v1 1회 + v2/v3 각 2회처럼)을 하나의 엑셀에 합치려면, 각
audit.json의 `results` 리스트에 `"sample"` 키를 태그로 추가해서 concat하고
`aggregates`를 `f"{version}__{sample}"` 키로 다시 집계해야 한다(export_audit_
to_excel.py의 `build_aggregates_rows()`/`_average_by_version()`이 이 형식을
지원함). 이번 세션엔 이 결합을 1회성 Python 스크립트로 했다(재사용 가능한
CLI로 만들지는 않았음 - 필요하면 다음 사람이 정식 스크립트로 만들 것).

## 5. 안 끝난 것 / 알려진 한계 (임의로 손대지 말고 사용자와 먼저 확인할 것)

- **v1 재현성 샘플이 1개뿐이다.** v1=0.328이 v2 평균(0.279)보다 높게 나왔는데
  (기대했던 v1<v2<v3 순서가 아님), v2 표본 편차가 원래 크다는 걸 이미 알고
  있어서(HANDOFF_CODEX.md) 이게 "진짜 v1>v2"인지 "우연히 v2가 낮게 나온
  샘플과 비교된 것"인지 아직 판단 불가. v1을 최소 1~2회 더 돌려봐야 함.
- **분기 진단 지표(§3-3)는 메인 점수의 중복 카운트 문제를 고치지 않는다** -
  진단만 함. 실제로 고치려면 분기-대-분기 매칭 알고리즘이 필요한데, 사용자가
  "새 알고리즘은 만들지 말라"고 명시적으로 결정해서 미뤄둔 상태.
- **CSS 셀렉터 기반 클릭의 `target_text` 추출이 안 된다** - 순수 CSS
  selector(예: `#cc-main-conversion-block > div > ...`)는 사람이 읽을 수
  있는 텍스트가 없어서 `common_signature()`가 `target_text: None`을 반환하고,
  이러면 클릭 여러 개를 등장 순서로만 배정한다(0131에서 실측 확인). 고칠
  방법이 마땅치 않아 미해결로 남김.
- **`judge_condition_equivalence()`(`critical_attribute/judge.py`)가
  완전히 미사용이다** - if/loop 조건식의 의미(변수명 달라도 논리적으로 같은
  판단인지)를 비교하는 함수가 있는데 실제 파이프라인 어디서도 안 부른다.
  조건식 자체는 비교 안 하고 분기 내부 액션만 본다(§3-3와 같은 맥락 - 사용자가
  GPT 상담 내용을 여러 차례 relay하며 검토했지만 "조건식 동등성까지는 굳이"로
  결론남, 분기 커버리지 정도로 충분하다고 판단함).
- **핵심업무 분류/분기 진단의 실제 정확도는 9개 케이스로만 검증됐다.** 사람
  검토본(`gold_core_actions/0089.json`, `0098.json`)과 대조해 52/52 일치를
  확인했지만, 이건 2개 케이스 기준이다 - 다른 7개 케이스는 사람 대조 없이
  LLM 판정을 그대로 신뢰한 상태.

## 6. 이번 세션에서 실제로 겪은 함정 (몰랐으면 시간 많이 날렸을 것들)

1. **LLM 프롬프트를 고치면 캐시(`gold_core_actions/_llm_classification_
   cache.json`)를 반드시 지워야 반영된다.** 실제로 Datetime 판정 버그를
   고치고 캐시를 안 지웠으면 옛날(틀린) 판정이 계속 재사용될 뻔했다.
2. **Docker `restart`는 `.env` 재로딩을 안 한다** - `env_file:`로 주입된
   환경변수는 컨테이너 생성 시점에 고정된다. `.env`를 고쳤으면 `docker
   compose up -d <service>`로 컨테이너를 다시 만들어야 한다.
3. **콘솔에 한글을 그대로 print하면 Windows cp949 인코딩으로 깨진다**(진짜
   내용은 멀쩡함, 표시만 깨짐). 확인하려면 파일로 써서 Read 도구로 읽을 것 -
   이 세션에서 여러 번 "깨진 줄 알았는데 실제로는 정상"이었던 경우와 "진짜로
   비어있던 경우"(엑셀 파일이 외부에서 수정된 흔적)를 둘 다 겪었으니, 겉보기
   깨짐만 보고 바로 문제라고 단정하지 말고 반드시 파일로 재확인할 것.
4. **`git merge-tree <base> <branch1> <branch2>`(3-인자 구버전)는 충돌을
   신뢰성 있게 못 잡는다** - 최소 git 2.38+의 `git merge-tree --write-tree
   <branch1> <branch2>`(2-인자, exit code로 충돌 여부 판단 가능)를 써야
   한다. 이 세션에서 3-인자 버전으로 "충돌 없음"을 잘못 확인했다가 나중에
   2-인자 버전으로 재확인해서 진짜 충돌 2건을 찾았다.
5. **공유 브랜치(`dev`) merge는 격리된 `git worktree`에서 먼저 시뮬레이션
   할 것.** 실제 작업 디렉터리에 uncommitted 변경사항이 있으면 `git
   checkout`으로 브랜치를 바꾸는 게 위험하다 - `git worktree add <임시경로>
   -b <임시브랜치> origin/dev` 로 완전히 격리된 곳에서 병합 시도 → 테스트 →
   검증까지 끝내고, 실제 push는 검증 후 같은 방식으로 재현해서 한다.
6. **대용량 산출물은 커밋 대상이 아니다** - `goldset_expansion/reports/`
   (원본 audit.json/CSV/MD, 케이스당 재현 가능), `goldset_expansion/
   export_main_challenge/`(34개 후보 원본), `runner/logs/`(에이전트 실행
   로그)는 전부 gitignore 취급(untracked로 두고 커밋 안 함). **오직
   `confirmed_goldset/evaluation_results/`만 "최종 압축 결과" 성격으로
   커밋한다.**
7. **이 저장소의 자동 PR 리뷰 봇은 CodeRabbit이 아니라 Qodo다**(`qodo-code-
   review[bot]`). 사용자가 "코드래빗 확인해"라고 해도 실제로는 Qodo 리뷰를
   봐야 한다 - 이번에 Qodo가 진짜 버그(WorFBench import 미보호)를 잡았다.
8. **엑셀 산출물을 사람이 열어볼 수 있다** - 이번에 최종 엑셀을 재확인했을 때
   V1 행 없이 이상한 텍스트가 들어가 있는 걸 발견했다(아마 사용자가 파일을
   열어봤을 것). "한 번 만들어서 검증했으니 끝"이 아니라, 최종 커밋 직전에
   다시 한 번 무결성을 확인하는 게 안전하다(원본 audit.json에서 재생성하면
   복구됨 - audit.json 자체는 아무도 안 열어보니 항상 정본으로 신뢰 가능).

## 7. 다음 작업으로 예고된 것 (2026-08-03 세션 말미에 사용자가 언급, 아직 구체화 안 됨)

사용자가 이 문서를 요청하면서 다음 작업을 짧게 언급했다:

> "그 다음 이제 평가/입력 데이터셋 관리 페이지랑 평가 페이지도 싹 갈아엎어야.
> 기능적으로 일단" + "ops-server 연동"

**아직 구체적 스펙이 없다** - 어느 저장소의 어느 페이지인지, "갈아엎다"가
정확히 뭘 의미하는지(UI 재설계? 백엔드 API 교체? 둘 다?), ops-server의 어떤
부분과 어떻게 연동하는지 전혀 확인 안 된 상태다. **다음 세션에서 이 작업을
시작하기 전에 반드시 사용자에게 범위를 먼저 확인할 것**:
- 어느 저장소/폴더의 어떤 페이지인가(프론트엔드 UI인지, 이 `agent_flow_eval`
  스크립트들을 감싸는 웹 UI를 새로 만드는 것인지, 아니면 `ops-server`
  자체의 기존 페이지를 고치는 것인지)
- "평가 페이지"가 이번 세션에서 만든 엑셀/마크다운 리포트를 웹으로 옮기는
  것인지, 아니면 완전히 다른 기존 기능인지
- ops-server 연동이 구체적으로 뭘 뜻하는지(API 호출? 같은 DB 공유? 배포
  파이프라인 통합?)

이번 세션의 산출물(9개 케이스 채점 파이프라인, 핵심업무 분류, 분기 진단,
WorFBench, 엑셀 리포트 생성기)은 전부 로컬 CLI/스크립트 기반이었다 - 웹
UI/ops-server 연동은 완전히 새로운 레이어를 얹는 작업이라는 점을 감안하고
스코프를 잡을 것.
