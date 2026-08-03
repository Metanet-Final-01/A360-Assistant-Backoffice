# Confirmed Goldset (2026-08-02 기준, 9개)

이 폴더는 **Claude-Codex 교차검수를 거쳐 최종 확정된 goldset 후보만** 담는다 —
`goldset_expansion/candidate_pool/`(34개 원본 후보 풀), `export_main_challenge/
deliverable/Main/`(34개 전체의 cleaned 변환본)과 다르다. 여기 있는 것만 v1/v2/v3
평가·PPT·발표의 공식 근거로 쓴다.

판단 근거와 최신 상태는 이 폴더가 아니라
`A360-Session-Notes/업무정의서/workflow_eval_harness_ppt/05_goldset_final_selection_note.md`
의 `## Claude-Codex 교차검수 결과` 섹션이 항상 최신·정본이다. 후보가 추가/제외될
때마다 그 문서가 먼저 바뀌고, 이 폴더는 그 결정을 그대로 반영한다.

## 현재 9개

`0131`, `0419`, `0140`, `0085`, `0098`, `0089`, `0112`, `0376`, `0164`
(각각 `briefs/<id>_<slug>.md`, `gold/<id>_<slug>__....goldset.json`)

**보류(아직 확정 아님, 여기 없음)**: `0080`, `0307` — 현재 채점기가 이 두 후보의
핵심 속성(연결 방식/DMN·Twilio 파라미터)을 비교 못 해서 보류.
**제외(확정)**: `0381` — 벤더 CRUD 예제 봇, 실제 업무 아님. `0164`는 한 차례
제외됐다가 재검토로 다시 포함됐다(0085/0112와 같은 "유틸리티성" 반박이 받아들여짐) —
자세한 경위는 위 note 파일 참고.

## 폴더 구조

```
confirmed_goldset/
  README.md          이 파일
  briefs/            9개 업무정의서 .md (사람이 실제 gold 액션 시퀀스를 보고 작성, 파싱 검증됨)
  gold/               9개 정답 goldset.json (실제 채점 기준 원본 워크플로우)
  pdfs/               렌더링된 PDF (gitignore됨 - 아래 명령으로 재생성)
  evaluation_results/ 압축된 채점 결과(F1/Precision/Recall 표 + 관찰). 원본 실행
                      로그(runner/logs/, 대용량·재현 가능)는 여기 안 넣는다 -
                      "최종 판단" 성격의 압축 결과만 커밋 대상.
```

## PDF 재생성

```bash
cd scripts/agent_flow_eval/goldset_expansion/scripts
python render_md_briefs.py
```

`briefs/*.md`를 읽어 `pdfs/*.pdf`로 렌더링한다(맑은 고딕/나눔고딕 필요 -
`render_task_briefs.py`의 `register_korean_font()` 참고). 백엔드가 `.md` 업로드를
지원하지 않아(PDF/PPTX/PPT/DOCX만 허용) `runner_v2.py`로 실제 v2/v3 평가를 돌리려면
이 PDF가 필요하다.

**주의**: PDF 파일명의 앞 4자리 ID(예: `0085_...`)가 `gold/`의 같은 ID와 매칭하는
유일한 키다. 이 ID를 유지하는 파일명 규칙을 바꾸지 말 것 — 예전에 렌더러가 이
접두사를 잘라내는 버그가 있었고(2026-08-02 수정됨), 후보-Gold 매칭이 깨진 적이
있다.

## 새 후보 추가하는 법

1. 위 note 파일에서 Claude+Codex 교차검수로 "확정" 판정을 받는다.
2. `briefs/<id>_<slug>.md`에 같은 양식으로 업무정의서를 작성한다(다른 파일들 참고).
3. `gold/`에 해당 gold `.goldset.json`을 복사한다.
4. `python render_md_briefs.py`로 PDF를 다시 생성한다.
5. note 파일의 "9 confirmed final" 목록과 개수를 갱신한다.

## 자체 제작 케이스 `9001` (2026-08-04 추가)

`9001_gold-price-lookup-and-send` — Bot Store가 아니라 **직접 제작한 봇**
("금 시세 조회 후 결과 발송")이다. Bot Store 코퍼스의 4자리 ID(0001~0470)와
겹치지 않도록 `9001`을 부여했다.

**이 케이스는 공식 9개 채점 집합(`FINAL_CASE_IDS`)에 아직 포함되지 않았다.**
`gold/`와 `briefs/`에 올려 두어 Agent 개발자가 채점에 사용할 수 있게 한 것이며,
공식 집합으로 승격하지 않은 이유는 다음과 같다.

- 기존 v1/v2/v3 실행(`runner/logs/gold_price_bot_*`, 2026-08-02)은 확정 9개와
  **모델과 조건이 다르다** — luna 계열 모델로 돌렸거나 v2/v3만 부분적으로
  temp0/seed0로 돌린 것이라, 9개(gpt-5.4-mini, temperature=0, seed=0,
  2026-08-03)와 같은 조건에서 비교할 수 없다.
- 공식 집합에 넣으면 macro 평균이 바뀌어 이미 배포된 리포트·엑셀·PR·Jira의
  수치가 전부 무효가 된다.

**승격하려면**: 9개와 동일한 조건으로 v1/v2/v3를 다시 실행한 뒤
`audit_final_goldset.py`의 `FINAL_CASE_IDS`에 `9001`을 추가하고 전체를
재채점한다. v1은 현재 표본이 1회뿐이라 어차피 추가 실행이 필요하므로, 그때
함께 처리하는 것이 효율적이다.

**업무정의서의 성격이 다르다**: 나머지 9개는 정답 워크플로우를 보고 사람이
업무정의서를 작성했지만, `9001`은 반대로 **업무정의서가 먼저 주어지고 그에 맞춰
봇을 제작**한 사례다. 따라서 정본은 `source_documents/9001_*.pdf`이고,
`briefs/9001_*.md`는 채점기가 텍스트로 읽기 위한 **전사본**이다. 내용을 고칠
일이 있으면 PDF를 기준으로 삼는다.

**알려진 미처리 사항 — 채점 전에 반드시 확인할 것**: 이 정답 워크플로우는
액션 35개 중 **22개(`Excel_MS.SelectRowColumnCellRange` 9회 +
`Recorder.capture` 8회 + `Keystrokes` 2회 등)가 엑셀 셀에 하나씩 테두리를
넣는 반복 블록**이다. 업무정의서 Task 3의 "엑셀 표 테두리 설정"이라는 **요구사항
1건**을 구현한 것인데, 현재 핵심업무 분류기는 이를 걸러내지 못한다 -
`AMBIGUOUS_GENERIC_ACTIONS` 목록이 Folder/File/String/Datetime 계열만 다루고
Excel 서식·Recorder 계열은 대상이 아니기 때문이다. 그 결과 분류기는 35개 중
34개를 핵심업무로 판정한다.

이대로 채점하면 Agent가 만점을 받으려면 셀 서식 반복 22개를 그대로 재현해야
하므로 Recall 분모가 부당하게 커진다. 이 문제는 재설계 계획서에도
`(SelectRowColumnCellRange+Recorder.capture)×7 ↔ 단일 서식 액션`이 **다대일
미해결**로 이미 기록되어 있다. 공식 집합으로 승격할 때 함께 해결해야 한다.

**참고**: 이 봇은 채점기 재설계의 **검증 기준**이었다. PM4Py를 폐기한 실측
근거(fitness 0.089 / precision 0.0)와 조건부 동치 규칙 2건
(`Recorder.capture(CLICK) ≡ WebAutomation.clickelement`,
`Recorder.capture(EXTRACT_TABLE) ≡ WebAutomation.gettablecontent`)이 모두
이 사례에서 도출되었다.
