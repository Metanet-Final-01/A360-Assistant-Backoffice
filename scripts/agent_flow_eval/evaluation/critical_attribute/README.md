# Critical Attribute Matching — 프로토타입 (2026-07-30)

GPT 재설계안("package.action만으로는 부족하다, Recorder 대상/Loop 유형까지 봐야
한다")을 구현한 프로토타입. **아직 core_task.py/run_eval_case.py 실제 채점 파이프라인에
연결되지 않았다** — 검토 후 승인되면 연결해야 함.

## 구성

- `attribute_signature.py` — raw.json/goldset.json 스텝의 `attributes`에서 critical
  field를 뽑는다. 실제 데이터로 검증 완료:
  - Recorder: `Test/botstore_deep` 39개 후보의 Recorder 액션 **36/36** 추출 성공.
    위젯별 액션 타입(`listviewAction`/`comboboxAction` 등 `...Action`으로 끝나는
    속성)과 uiObject(base64 blob)를 디코딩해 css_selector/dom_xpath/html_tag/
    frame_src(실제 웹사이트 URL) 등을 꺼낸다.
  - Loop: **41/56** 추출 성공 (나머지 15개는 `anchor`만 있고 loopType이 아예 없는
    구조적 마커 노드 - 버그 아니고 실제 데이터가 그럼). loop_type은 실측상 WHILE/
    ITERATOR만 존재, 고정횟수(TIMES류) 사례는 이번 39개 안에 없었음 — "횟수 조건
    이면 IF로 평탄화 가능"이라는 논의는 지금 후보군에는 해당 사례가 없다.
  - StructuredDataExtraction 등 다른 패키지는 39개 안에 실제 사례가 없어서
    구현하지 않음 (추측 금지 원칙).
- `compare.py` — 정적 규칙으로 판정 가능한 필드(widget_action/frame_src/dom_xpath/
  loop_type/loop_target)는 match/mismatch, selector 문자열이 다르면 uncertain으로
  표시(정적으로 동치 판정 불가 - LLM judge 필요 지점을 정직하게 노출). **여러 필드를
  하나의 종합 점수로 뭉개지 않는다** — GPT가 지적한 "근거 없는 가중합 금지"를
  그대로 지킴, `summary_counts()`로 개수만 집계.
- `judge.py` — 정적 규칙으로 안 되는 경계(selector 동치, 조건식 동치)에 로컬 Exaone
  (`http://192.168.1.147:8820/v1`)를 호출. **액션조합-동일의도 판정과 Python/JS/DLL
  내부의미 판정은 아직 미구현** — 실제로 비교할 predicted workflow가 없어서 사용처가
  없기 때문 (없는 걸 미리 만들지 않음).
- `demo_critical_attribute.py` — 자체 테스트. 정답 vs 정답 자기자신(전부 match),
  정답 vs selector만 다르게 조작한 가짜 예측(uncertain 나오는지) 확인. 로컬 Exaone
  서버가 지금 꺼져있어서(APITimeoutError) judge 실제 호출까지는 검증 못 함 —
  와이어링 자체는 맞게 됨(에러 핸들링까지 정상 동작 확인).

## 아직 안 된 것 (검토/결정 필요)

1. **실제 predicted workflow가 없음.** v1/v2/v3 배치 채점은 원래 13개 goldset에만
   돌렸고, 이번 39개(Main-18/Challenge-21)에는 에이전트 예측이 아직 없다. 그래서
   이 모듈은 "정답끼리 비교"까지만 검증됐고 진짜 채점 시나리오(정답 vs 에이전트
   예측)는 아직 못 돌려봄.
2. **pm4py/WorFBench 연결 안 함.** GPT안은 "구조는 pm4py/WorFBench 유지, critical
   attribute는 별도 독립 지표로 보고"였는데, 이 프로토타입은 critical attribute
   비교기만 만든 상태고 기존 두 어댑터의 라벨을 실제로 강화하는 작업(예:
   `convert_to_pm4py.py`/`convert_to_worfbench.py`가 풍부해진 라벨을 받아 구조
   점수와 attribute 점수를 나란히 리포트하는 것)은 아직 안 건드림 — 기존 v1/v2/v3
   점수에 영향 없음, 손대기 전에 검토 필요.
3. **PPT 내용 미반영.** `A360-Session-Notes/10-workflow-eval-ppt-prep.md`는 아직
   이번 재설계 논의 전이라 그대로임.
4. **LLM judge 2종 미구현** (위 참고).
