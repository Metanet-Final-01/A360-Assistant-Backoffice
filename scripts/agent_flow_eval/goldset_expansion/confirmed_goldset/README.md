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
