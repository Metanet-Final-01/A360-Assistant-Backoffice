# RPA 워크플로우 생성 평가 — 전체 설명서

이 폴더 하나만 읽으면 **"봇 zip 파일에서 시작해 에이전트 점수가 나오기까지"**
전 과정과, 그 과정에서 정한 규칙들이 왜 타당한지를 알 수 있다. 사전 지식 없이
읽을 수 있게 썼다.

## 한 문단 요약

Automation Anywhere Bot Store에서 받은 봇 zip 470개를 풀어서, 사람이 읽을 수
있는 **업무정의서**와 기계가 채점할 수 있는 **정답 워크플로우(goldset)** 를
만들었다. 이 중 사람이 두 번 교차검수해 확정한 **9개**를 기준으로, 업무정의서를
AI 에이전트(v1/v2/v3)에게 주고 워크플로우를 만들게 한 뒤, 정답과 얼마나
일치하는지 **Precision / Recall / F1** 으로 점수를 매긴다.

## 읽는 순서

| # | 문서 | 내용 |
|---|---|---|
| 1 | [01-overview.md](01-overview.md) | 전체 흐름 한 장 (zip → 점수) |
| 2 | [02-goldset.md](02-goldset.md) | 정답 데이터셋을 어떻게 만들었나 |
| 3 | [03-preprocessing.md](03-preprocessing.md) | 채점 전에 무엇을 왜 걸러내나 |
| 4 | [04-scoring.md](04-scoring.md) | 우리가 만든 지표와 계산 방식 |
| 5 | [05-validation.md](05-validation.md) | 이 규칙들이 타당한지 어떻게 확인했나 |
| 6 | [06-results-and-limits.md](06-results-and-limits.md) | 실제 점수와 한계 |

부록 (본문을 다 읽은 뒤 필요할 때만)

| 부록 | 내용 |
|---|---|
| [appendix-a-worfbench.md](appendix-a-worfbench.md) | 외부 벤치마크 WorFBench — 왜 우리 점수와 나란히 비교하면 안 되는가 |
| [appendix-b-pm4py-rejected.md](appendix-b-pm4py-rejected.md) | PM4Py를 도입하려다 완전히 폐기한 근거 |
| [appendix-c-rejected-designs.md](appendix-c-rejected-designs.md) | 만들었다가 실측 후 되돌린 설계들 (다시 시도하지 말 것) |

## 지금 상태 (2026-08-04)

- **확정 정답 케이스 9개**: `0085 0089 0098 0112 0131 0140 0164 0376 0419`
- **공식 점수 (Rule-only Macro F1)**: v1 `0.328` · v2 `0.279` · v3 `0.414`
- **정답 원본 위치**: `goldset_expansion/confirmed_goldset/gold/`
- **채점 스크립트**: `evaluation/audit_final_goldset.py`
- **최종 리포트**: `goldset_expansion/confirmed_goldset/evaluation_results/`

작업 인수인계용 상세 기록(어떤 명령을 어떤 순서로 돌리는지, 세션 중 겪은
함정)은 이 폴더가 아니라 [`../evaluation/HANDOFF_NEXT_20260803.md`](../evaluation/HANDOFF_NEXT_20260803.md)
에 있다. **이 폴더는 "무엇을 왜 그렇게 했는가"를, 그 문서는 "어떻게
이어서 작업하는가"를 다룬다.**

## 이 프로젝트가 지키는 원칙

문서 곳곳에서 반복되는 판단 기준이라 먼저 밝혀둔다.

1. **규칙은 초보자도 한 줄로 이해할 수 있어야 한다.** 설명이 길어지면 그 규칙이
   틀렸다는 신호로 본다.
2. **근거 없는 숫자를 만들지 않는다.** "절반 이상이면 인정" 같은 임의 임계값을
   두지 않는다. 근거를 댈 수 없으면 그 지표를 아예 안 만든다.
3. **점수를 유리하게 만들려고 규칙을 바꾸지 않는다.** 기대와 다른 결과가 나와도
   그대로 보고한다.
4. **주장하기 전에 실제 데이터로 확인한다.** 이 문서의 모든 "확인했다"는
   실제로 돌려본 결과이고, 확인 방법도 같이 적었다.
5. **가정으로 코드를 미리 복잡하게 만들지 않는다.** 아직 일어나지 않은 문제를
   위한 방어 코드는 넣지 않고, 대신 한계로 기록한다.
