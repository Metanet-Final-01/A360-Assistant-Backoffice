# 1. 전체 흐름 — zip 파일에서 점수까지

## 우리가 답하려는 질문

> **"업무정의서를 주면, AI 에이전트가 사람이 만든 것과 비슷한 RPA 워크플로우를
> 만들어낼 수 있는가?"**

이걸 재려면 세 가지가 필요하다.

1. **문제지** — 사람이 읽을 수 있는 업무정의서
2. **정답지** — 그 업무를 실제로 수행하는 검증된 워크플로우
3. **채점 기준** — 에이전트의 답과 정답을 비교하는 방법

이 프로젝트는 이 셋을 전부 실제 상용 봇에서 만들어냈다.

## 전체 그림

```
[1] Bot Store 봇 zip 470개
        |  압축 해제 + 워크플로우 추출
        v
[2] 원본 워크플로우 JSON (462개 봇 / 818개 워크플로우)
        |  공통 규칙으로 정규화
        v
[3] goldset.json 817개          <-- 기계가 읽는 "정답 후보"
        |  후보 선별 + 사람 교차검수
        v
[4] 확정 9개 (정답 + 업무정의서)  <-- 공식 평가 기준
        |
        |  업무정의서를 PDF로 만들어 에이전트에게 입력
        v
[5] 에이전트 v1 / v2 / v3 실행
        |  응답을 goldset.json과 같은 형식으로 변환
        v
[6] 예측 워크플로우
        |
        +--> [7] 채점: 정답 vs 예측 --> Precision / Recall / F1
```

## 각 단계가 하는 일

### [1] → [2] 봇 zip 풀기

Bot Store에서 받은 봇은 `.zip` 안에 워크플로우 정의와 실행에 필요한 각종
파일이 섞여 있다.

- `processing/unpack_all_downloads.py` — zip을 푼다. 봇 로직과 무관한 대용량
  `.jar`(자바 라이브러리)는 건너뛴다. 이게 용량 대부분을 차지하는데 워크플로우
  분석에는 전혀 안 쓰이기 때문이다.
- `processing/extract_workflows.py` — 풀린 폴더에서 실제 워크플로우 정의만
  골라낸다.

결과: 462개 봇에서 818개 워크플로우를 얻었다(470개 중 일부는 워크플로우가 없거나
형식이 달라 제외됨).

### [2] → [3] 정규화해서 goldset 만들기

원본 워크플로우는 Automation Anywhere 내부 형식이라 그대로는 비교하기 어렵다.
`processing/normalize_extracted_workflows.py`가 **공통 형식(goldset.json)** 으로
바꾼다.

goldset.json은 이렇게 생겼다 — 트리 구조다.

```json
{
  "steps": [
    { "type": "action", "package": "Browser", "action": "openbrowser", "uid": "..." },
    { "type": "if",
      "steps":    [ { "type": "action", "package": "File", "action": "copy" } ],
      "branches": [ { "branch": "else",
                      "steps": [ { "type": "action", "package": "File", "action": "delete" } ] } ] }
  ]
}
```

핵심 개념 두 가지만 알면 된다.

- **액션(action)** — 실제로 무언가를 하는 한 단계. `패키지.액션` 형태로 부른다
  (예: `Browser.openbrowser` = 브라우저 열기).
- **제어 구조** — `if`(조건 분기), `loop`(반복), `try`(오류 처리). 이것들은
  자기가 직접 일을 하지 않고 안에 액션을 담는다.

**중요**: 정답과 예측 **양쪽 모두** 이 같은 변환기를 통과한다. 한쪽만 다르게
처리하면 비교가 성립하지 않기 때문이다.

### [3] → [4] 9개 확정

817개를 전부 쓰지 않는다. 자세한 선별 과정은 [02-goldset.md](02-goldset.md)에
있고, 요점만 말하면 **"진짜 업무를 하는 봇인가"** 를 사람이 두 번(Claude와
Codex가 독립적으로) 검토해서 **둘 다 인정한 것만** 남겼다.

각 확정 케이스는 두 파일을 가진다.

- `confirmed_goldset/briefs/<id>_<이름>.md` — **업무정의서**(문제지). 사람이
  실제 정답 액션을 보면서 직접 썼다.
- `confirmed_goldset/gold/<id>_<이름>.goldset.json` — **정답 워크플로우**(정답지)

### [4] → [5] 에이전트 실행

업무정의서를 PDF로 렌더링해서 백엔드 에이전트에 넣는다. 프론트엔드가 하는 것과
똑같은 방식으로 호출한다(`runner/runner_v2.py`).

- **v1 / v2 / v3** 는 에이전트 버전이다. 셋 다 **같은 LLM(`gpt-5.4-mini`,
  temperature=0, seed=0)** 을 쓴다 — 모델 차이가 아니라 **에이전트 설계 차이**만
  비교하기 위해서다.

### [5] → [6] 예측을 같은 형식으로 변환

에이전트 응답은 goldset.json 형식이 아니다. `processing/convert_backend_
recommendation.py`가 **정답과 똑같은 규칙으로** goldset.json 형식으로 바꾼다.

### [6] → [7] 채점

`evaluation/audit_final_goldset.py`가 정답과 예측을 비교해서 점수를 낸다.
자세한 내용은 [03-preprocessing.md](03-preprocessing.md)와
[04-scoring.md](04-scoring.md)에 있다.

## 어떤 스크립트를 언제 쓰나

| 하고 싶은 것 | 쓸 것 |
|---|---|
| 9개 케이스 채점하기 | `evaluation/audit_final_goldset.py` |
| 채점 결과를 엑셀로 | `evaluation/export_audit_to_excel.py` |
| 에이전트 재실행 | `runner/run_runner_v2_batch.py` |
| 봇 zip부터 다시 | `processing/unpack_all_downloads.py` → `extract_workflows.py` → `normalize_extracted_workflows.py` |

**주의**: `evaluation/run_eval_batch.py`는 **9개 케이스용이 아니다.** 옛날 13개
케이스 전용이고 폴더 구조 자체가 다르다. 9개는 반드시
`audit_final_goldset.py`를 쓴다.

## 지금 나온 점수

| 에이전트 | Rule-only Macro F1 | 실행 횟수 |
|---|---:|---|
| v1 | 0.328 | 1회 |
| v2 | 0.279 (0.233 ~ 0.326) | 2회 |
| v3 | 0.414 (0.367 ~ 0.462) | 2회 |

"Rule-only"가 뭔지, 왜 이 숫자가 낮아 보이는지는
[06-results-and-limits.md](06-results-and-limits.md)에서 설명한다.
