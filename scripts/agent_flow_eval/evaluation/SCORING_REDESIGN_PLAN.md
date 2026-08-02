# RPA 워크플로우 채점 재설계 (v4 — 3차 GPT 검토 반영, Judge Match 단순화)

## Context

`run_eval_case.py`의 기존 채점(package.action multiset/LCS + PM4Py + WorFBench)이
"금 시세 조회 봇" 실제 테스트(gold vs gpt-5.6-luna 예측)에서 PM4Py fitness 0.089/
precision 0.0로 사실상 무의미하다는 게 실증됨 — 사람이 고른 구현 1개와 에이전트가
고른 다른 구현을 비교하는데 정합성 체크 도구를 쓰다 보니 정상적인 구현 차이까지
전부 deviation으로 잡힘.

계획을 2차례 GPT 검토에 돌렸고, 사용자 본인이 직접 실제 데이터를 검증하며 걸러낸
결과 다음이 확정됨(중요 — 실제로 확인한 사실, 추측 아님):
- 금시세봇 실제 Recorder.capture 3회: `linkAction=CLICK` ×2, `tableAction=EXTRACTTOCSV`
  ×1. 예측의 `WebAutomation.clickelement` ×2 / `gettablecontent` ×1과 개수·순서·
  의미가 맞아떨어짐 — 근거 있는 조건부 동치 후보.
- 정답 `Excel_MS.CreateSpreadsheet`(`filePath=file://$localFilePath$`,
  `isOverwrite=true`) vs 예측 `Excel_MS.OpenSpreadsheet`(**`filePath: null`**,
  실제로 확인함) — **동치 아님, 예측 쪽의 진짜 미완성 구현**. 동치 규칙에 넣지
  않는다.
- GPT가 초안에서 예시로 든 `GETTOTALITEMS↔gettablecontent`는 **다른 bot(0020)의
  데이터를 착각해서 쓴 것**이었음 — 실제 금시세봇 데이터로 교체함.

2차 GPT 검토(사용자가 "gpt 의견이니 걸러들을" 이라며 직접 필터링 지시)에서
반영할 것과 반영 안 할 것을 구분함:
- **반영**: 같은 canonical label끼리 무작정 순서대로 짝짓지 말 것(상세비교/순서
  계산이 왜곡됨), Judge Match 배정은 그리디가 아니라 진짜 최대 이분매칭이어야
  함(외부 의존성 없이 DFS augmenting path로 충분), Judge 프롬프트가 "상위 업무
  목적 공유"만으로 다대일 구현차이를 단일 액션 동치로 오판하지 않게 명시적으로
  막을 것, Gold의 Core Action 포함/제외 여부를 매번 다시 계산하지 말고 고정
  파일로 남길 것.
- **필터링(과설계로 판단)**: `accepted_realizations` 패턴 DSL, `reason_codes`/
  `evidence_ids`/`cache_key` 재현성 스캐폴딩, Required Order/Coverage 이분화 —
  사용자 본인 지침("복잡하면 설명하기 어려움")에 따라 전부 보류.
- LIS vs LCS는 GPT가 두 번 말을 바꿔서(사용자가 직접 지적) 사용자가 "둘 다
  구해보자"고 결정 — 아래 §5에 반영. 실제로는 §1이 이미 1:1 매칭을 확정하므로
  두 계산 결과가 수학적으로 같아야 정상(하나의 검증 포인트로 활용).

## 최종 최소 구조 (한눈에 보기)

1. Gold Core Action 고정 (§7, uid별 include/exclude)
2. Rule Match (canonical label + 조건부 Action Equivalence, signature-aware 배정)
3. Judge Match (Rule로 안 잡힌 것만, 유사도 기준 상호 Top-1 후보에만 LLM 호출 → same이면 매칭)
4. Action P/R/F1 (1차 확정 지표)
5. Action Chain F1 (확정된 매칭 쌍으로 LIS)
6. LCS (LIS 구현 검증용 교차체크, 별도 지표 아님)
7. WorFEval Chain F1 (외부 참고 결과로만 표시)

Kuhn's algorithm/networkx/최대이분매칭/ambiguous 처리 같은 개념은 전부 뺐다 —
후보를 LLM 호출 전에 상호 Top-1로 미리 하나로 좁히므로 "여러 same 쌍 중 뭘
고를지" 문제 자체가 생기지 않는다.

## 용어 (변경 없음)

Gold Workflow / Core Action / Action Equivalence / Rule Match / Judge Match /
Unmatched. 별도 "Key Property" 용어 안 만듦.

## 설계

### 1. 매칭 엔진 — 명시적 1:1 쌍 생성 (Counter는 회귀검증에만)

```python
@dataclass
class ActionMatch:
    gold_id: str
    pred_id: str
    match_type: Literal["rule", "judge"]
    canonical_label: str
```

**1a. Rule Match 배정** — canonicalize(`action_equivalence_rules.json` + §4
조건부 규칙)로 같은 canonical label이 된 gold/pred 액션들 중:
1. `common_signature()`(§3)가 완전히 같은(operation+target_text 동일) 쌍부터
   우선 배정.
2. 남은 것은 등장 순서대로 배정.

(발생 순서만으로 무작정 짝짓지 않음 — 순서가 다른 동일-라벨 액션들의 상세비교/
Chain 계산이 왜곡되는 문제를 방지.)

**1b. Judge Match 배정 — 유사도 기준 "상호 Top-1" 후보만 LLM에 물어봄**

Rule Match(1a)로 안 잡힌 남은 gold/pred 액션들끼리만 대상(같은 패키지로
필터링하지 않음 — Recorder vs WebAutomation이 핵심 케이스). 다대다 후보를
LLM에 다 물어보거나(O(n·m) 호출) 최대이분매칭으로 배정하는 대신, **후보를
LLM 호출 전에 유사도로 먼저 상호 Top-1까지 좁힌다**:

1. 남은 각 gold/pred 액션마다 `"{package.action} {operation} {target_text}"`
   하나의 텍스트로 합치고(§3의 `common_signature()` 값 사용), **임베딩
   cosine similarity 하나만** 유사도 점수로 쓴다(문자열유사도+operation+
   target_text+앞뒤액션을 따로 가중합하지 않음 — 임의 가중치 규칙이 또
   늘어나는 걸 피함). 임베딩 모델은 앞서 논의한 OpenAI `text-embedding-3-small`
   재사용.
2. gold 액션 A의 최고 유사 후보가 pred B이고, **동시에** pred B의 최고 유사
   후보가 gold A일 때("상호 Top-1") **+ 그 유사도가 최소 기준(threshold) 이상**
   일 때만 그 쌍을 LLM Judge 후보로 채택. 상호 Top-1이어도 유사도 자체가
   기준 미만이면(남은 후보 중 억지로 1등이 된 것뿐인 경우) 바로 Unmatched로
   둔다 — threshold 구체값은 실제 13개 goldset 사례를 돌려보면서 정한다
   (지금 추측으로 고정 안 함).
3. 상호 Top-1이 아니거나 threshold 미만인 쌍은 LLM 호출 자체를 안 함(비용
   절감 — 남은 액션이 각각 20개면 최대 400회 대신 최대 20회 이하로 줄어듦).

**주의**: 이 임베딩(`text-embedding-3-small`)은 §8의 실제 WorFBench 라이브러리가
쓰는 임베딩(`all-mpnet-base-v2`, 임계값 0.6)과 **완전히 별개 경로**다. WorFEval은
"외부 벤치마크 그대로 재현"이 목적이라 모델/임계값을 바꾸지 않고 원본 그대로
유지한다(§8) — 여기 §1b의 임베딩은 순전히 우리 자체 Action 평가에서 LLM 호출
후보를 줄이기 위한 용도로, 두 경로를 섞지 않는다.
4. 채택된 쌍만 `judge_action_equivalence()`(신규, 기존
   `judge_selector_equivalence` 패턴)에 넘겨 same/different/uncertain 판정 →
   same이면 Judge Match, 아니면 Unmatched.

이 방식은 애초에 한 액션당 후보를 1개로 좁혀서 LLM에 넘기므로, 이전에
검토했던 "Judge가 여러 쌍을 다 same이라 했을 때 어떻게 배정할지"(최대이분매칭
vs 서로 유일한 same쌍만 인정) 문제 자체가 발생하지 않는다 — 그 두 대안은
전부 필요 없어져서 뺀다. ambiguous 로그도 별도로 안 만듦(상호 Top-1이
아니면 애초에 Judge를 안 부르고 바로 Unmatched).

**TP = 1a 배정 수 + 1b 배정 수, FP = 남은 pred, FN = 남은 gold.**
**Action Precision/Recall/F1** — 1차 확정 지표.

### 2. "여러 액션 ↔ 액션 하나" 패턴 — 자동화 안 함, Judge가 잘못 봐주지 않게 막음

`DataTable.deleteRow×3 ↔ SetCellFormula`, `(SelectRowColumnCellRange+
Recorder.capture)×7 ↔ usingFormatCellAction` 같은 다대일 구현차이는 패턴 DSL을
새로 안 만들고 이번 v1은 **Unmatched로 고정**한다. 대신 Judge 프롬프트(§6)에
"상위 업무 목적이 같다는 이유만으로 단일 액션 동치 판정 금지, 원자적 동작 대체
가능성만 볼 것"을 명시해서 Judge가 이 정책을 몰래 어기지 않게 막는다. 검증표
(맨 아래)에서도 이 두 케이스는 "Unmatched"로 확정 기대값을 둔다 — Judge가 여기서
same이라고 하면 그건 프롬프트 버그다.

### 3. attribute(상세작업) 비교 — 최소 공통 스키마 + 안전한 결측 처리

```python
def common_signature(step: dict) -> dict:
    return {"operation": ..., "target_text": ...}
```
- **operation은 패키지 내부 원본값이 아니라 추상화된 상수로 통일**(예:
  `Recorder`의 `tableAction=EXTRACTTOCSV`나 `WebAutomation.gettablecontent` 둘
  다 → `"EXTRACT_TABLE"`; `linkAction=CLICK`/`buttonAction=CLICK`/
  `WebAutomation.clickelement` → `"CLICK"`). 패키지별 작은 매핑 딕셔너리만
  있으면 됨 — 새 추상 계층이 아니라 비교를 위한 최소 정규화.
- **target_text 비교 규칙**: 양쪽 다 값 있고 같으면 `match`, 양쪽 다 있고
  다르면 `mismatch`(또는 Judge로), **한쪽이라도 값이 없으면 `not_applicable`**
  (빈 문자열과 비교해서 `mismatch`로 잘못 떨어뜨리지 않음).
- 이번엔 Recorder + WebAutomation만 만듦(이번 사례에서 실제 필요한 만큼).
  Excel_MS/Gmail은 이번 매칭 쌍에서 안 씀 — 필요해지면 그때 추가.

기존 `compare.py`의 `match/mismatch/uncertain/not_applicable` 원시 카운트
원칙 유지, 새 지표 이름 안 만듦.

### 4. `action_equivalence_rules_conditional.json` — 대칭 구조, 검증된 것만

```json
{
  "conditional_equivalence_groups": [
    {
      "canonical": "web.click",
      "members": [
        {"package": "Recorder", "action": "capture", "when": {"operation": "CLICK"}},
        {"package": "WebAutomation", "action": "clickelement", "when": {"operation": "CLICK"}}
      ]
    },
    {
      "canonical": "web.extract_table",
      "members": [
        {"package": "Recorder", "action": "capture", "when": {"operation": "EXTRACT_TABLE"}},
        {"package": "WebAutomation", "action": "gettablecontent", "when": {"operation": "EXTRACT_TABLE"}}
      ]
    }
  ]
}
```
`when.operation`은 §3의 정규화된 값을 씀. `CreateSpreadsheet ↔ OpenSpreadsheet`는
**등록 안 함**(확인 결과 동치 아님).

### 5. 순서(order) — Action Chain: LIS(주) + LCS(교차검증용) 둘 다 계산

§1에서 이미 1:1 매칭이 확정되므로(Rule+Judge), 실제 WorFBench의
`t_eval_plan()`(`a360-eval-sandbox/external/WorFBench/evaluator/graph_evaluator.py:163-214`)
과 같은 방식으로 **LIS**를 주 계산으로 쓴다:
1. 매칭된 쌍들을 **예측 순서대로** 정렬.
2. 각 쌍의 **정답 인덱스**를 뽑아 수열을 만듦.
3. 이 수열의 **최장 증가 부분수열(LIS) 길이**를 구함(O(n log n), patience
   sorting).
```
Action Chain Precision = LIS길이 / 예측 Core Action 수
Action Chain Recall    = LIS길이 / 정답 Core Action 수
Action Chain F1        = 조화평균
```
**교차검증**: 같은 매칭 쌍 위에서 LCS-DP(`can_match(gold[i],pred[j])` = 확정된
매칭 쌍인가)로도 같은 값을 계산해서 **LIS 결과와 정확히 같은지 확인**하는
단위테스트를 추가한다(매칭이 이미 1:1로 확정된 상태에서는 두 방법이 수학적으로
동일한 값을 내야 정상 — 다르면 매칭 로직에 버그가 있다는 신호).

**해석 주의**: Action Chain F1은 "순서 정확도"가 아니라 **"액션 선택 + 상대
순서를 함께 반영한 지표"**다(액션 누락/불필요 액션도 분모에 영향을 줌) — 리포트
문서화 시 이렇게 정확히 설명한다. 순수 순서만 보는 지표가 필요하면 나중에
별도로 추가.

Required Order(진짜 필수 선후행 관계) 자동도출은 여전히 폐기 — 근거(produces/
consumes, 사람 검수 등) 생기면 나중에 추가.

### 6. Judge 프롬프트 — 원자적 동작 대체가능성만 판단, 실제 액션 우선

`judge_action_equivalence()` 프롬프트에 명시:
- "두 액션이 같은 상위 업무 목적에 기여하는지가 아니라, **각 액션 하나가 수행하는
  원자적 동작이 서로 대체 가능한지**만 판단하라."
- "여러 액션으로 이뤄진 구현의 일부와 단일 액션을 비교하는 경우, 그 하나만으로
  대체된다고 확신할 수 없으면 different 또는 uncertain으로 판단하라."
- "label/rationale/notes에 어떤 업무가 언급돼 있어도, 실제 package.action 목록에
  없으면 그 업무가 수행된 것으로 판단하지 마라." (금시세봇 예측의 "발송" 라벨
  누락 사례로 실제 확인된 문제)

Judge API 실패 시 Rule-only 결과만 출력, Judge 부분은 "판정불가" 표시(단순
try/except, 새 상태 enum 안 만듦). 응답은 기존 `judge.py` 패턴 그대로
`{"verdict", "reason"}`만 — `reason_codes`/`evidence_ids`/`cache_key` 같은
재현성 스캐폴딩은 이번엔 안 만듦(단순화 우선, 필요해지면 추가).

### 7. Gold Core Action 선택 — 고정 파일로 남김 (매번 재계산 안 함)

Gold 원본 액션 중 어떤 게 "업무 핵심"이고 어떤 게 "구현 기교/디테일"인지는
매 실행마다 다시 판단하지 않고, **UID 단위의 간단한 include/exclude 목록**으로
한 번 고정한다:
```json
{
  "source": "gold_normalized.json",
  "core_actions": [
    {"uid": "978b5356-...", "include": true},
    {"uid": "40dcc891-...", "include": false, "reason": "manual_formatting_detail"}
  ]
}
```
복잡한 패턴 DSL 없음 — uid별 true/false + 선택적 reason 문자열뿐. 이번 금시세봇
사례에 대해 이 파일을 사람이 직접 한 번 작성(반복 서식 액션 7세트는
`include:false`, Gmail/저장 액션은 `include:true` 등) — 이걸 고정해야 Action
P/R/F1 분모가 실행마다 흔들리지 않고, 기대표와 재현 비교가 가능해진다.

### 8. 액티브 지표에서 제외 (변경 없음)

- PM4Py: 코드 유지, 최종 리포트/CLI 요약에서 제외.
- `core_task.py`(전체) + `run_eval_case.py`의 `package_family()`/
  `salient_families()`: 근거 없는 하드코딩이라 삭제(다른 import 없는지 grep 확인 후).
- 실제 WorFBench 라이브러리(`score_worfbench_f1chain`): 계속 계산하되 "외부
  벤치마크 비교용"으로 명확히 분리 표시.

## 변경/생성 파일

- **신규** `evaluation/action_matching.py`: §1 (ActionMatch, signature-aware
  Rule 배정, 유사도 상호 Top-1 사전필터 + Judge 호출, Action P/R/F1). 기존
  `load_action_equivalence_map`/`canonicalize_actions`을 여기로 이전, 다른
  곳은 import.
- **신규** `evaluation/action_chain.py`: §5 (LIS 주 계산 + LCS-DP 교차검증,
  둘이 같은지 확인하는 assert 포함).
- **신규** `evaluation/action_equivalence_rules_conditional.json`: §4.
- **신규** `evaluation/gold_core_actions/<goldset_id>.json`: §7 (금시세봇
  사례 1개부터 시작).
- **수정** `critical_attribute/attribute_signature.py`: `common_signature()`
  추가(Recorder+WebAutomation, operation 정규화 매핑 포함).
- **수정** `critical_attribute/compare.py`: `compare_common()` 추가(target_text
  결측 시 not_applicable 처리 포함).
- **수정** `critical_attribute/judge.py`: `judge_action_equivalence()` 추가,
  §6 프롬프트 반영.
- **수정** `run_eval_case.py`: PM4Py 제외, 신규 모듈 호출로 교체,
  `salient_families`/`package_family` 삭제.
- **삭제** `core_task.py` (grep 확인 후).
- **수정** `adapters/pm4py_adapter.py`, `worfbench_adapter.py`: core-only
  호출부 제거, worfbench_adapter는 "외부 참고" 표시만 추가.

## 검증 방법 — 기대표 고정, 점수 절대값 아님

금시세봇 사례에 대해 기대 판정을 **모호함 없이 고정**한다(다대일 항목도
"Unmatched 또는 Judge가 인정할 수도"가 아니라 **확정적으로 Unmatched**):

| Gold 액션 | 예측 액션 | 기대 판정 |
|---|---|---|
| Browser.openbrowser | Browser.openbrowser | Rule Match |
| Recorder.capture(operation=CLICK) ×2 | WebAutomation.clickelement ×2 | Rule Match (조건부, target_text로 올바른 쌍 배정 확인) |
| Recorder.capture(operation=EXTRACT_TABLE) | WebAutomation.gettablecontent | Rule Match (조건부) |
| Excel_MS.writeDataTableToWorksheet | Excel_MS.writeDataTableToWorksheet | Rule Match |
| Excel_MS.CreateSpreadsheet(경로 있음) | Excel_MS.OpenSpreadsheet(filePath=null) | **Unmatched** (확정, 동치 아님) |
| DataTable.deleteRow×3/insertRow×2 | Excel_MS.SetCellFormula | **Unmatched** (확정, 다대일 미해결) |
| (SelectRowColumnCellRange+Recorder.capture)×7 | "...usingFormatCellAction" | **Unmatched** (확정, 다대일 미해결 — Judge가 same이라 하면 프롬프트 버그) |
| Excel_MS.SaveSpreadSheet, CloseSpreadsheet | 없음 | FN |
| Gmail.Connect/Send/Disconnect | 없음 | **FN** (Judge가 label의 "발송"에 속으면 안 됨) |

실행 후 이 표와 실제 출력을 대조 — 다르면 매칭 로직/동치 규칙/Judge 프롬프트 중
어디가 문제인지 진단한다. 이게 유일한 acceptance criterion(점수가 PM4Py보다
높다는 건 기준 아님).

추가 검증:
- LIS와 LCS-DP가 같은 매칭 쌍 위에서 동일한 chain_tp를 내는지 단위테스트.
- 기존 13개 goldset의 저장된 v1/v2/v3 예측 결과에 새 파이프라인을 돌려서
  에러 없이 리포트가 생성되는지 회귀 확인.
