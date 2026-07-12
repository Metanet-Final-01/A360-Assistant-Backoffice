# RAG 검색 품질 평가 (RAGAS)

pm4py/WorFBench(`app/eval/`)가 "에이전트가 만든 워크플로우가 정답과 맞는가"를 본다면,
이건 "RAG 검색이 질문에 맞는 문서를 찾아오는가, 그 문서로 답을 만들 수 있는가"를 본다
— 서로 다른 축이라 별도 서브패키지로 분리했다.

## 왜 pip 버전을 정확히 이렇게 고정했는가

`ragas>=0.3`은 `ragas.llms.base`가 무조건 `langchain_community.chat_models.vertexai`를
임포트하는데, 그 서브모듈이 `langchain-community 0.4+`에서 별도 패키지로 분리되면서
없어져 `ImportError`가 난다(2026-07-12에 직접 재현·확인). `ragas==0.2.15` +
`langchain-community==0.3.19` + `langchain<0.4` 조합으로 고정해야 실제로 임포트된다.
`requirements.txt`를 건드릴 때 이 조합을 깨지 않도록 주의.

## 실행 흐름 (`runner.py`)

1. `cases/rag_goldset_v1.json`의 케이스마다 A360-Assistant-Backend의
   `GET /api/rag/search`를 호출해 **실제 운영 검색 파이프라인**(pgvector+BM25 RRF+
   Voyage 리랭크)이 찾아온 문서를 그대로 가져온다.
2. 그 문서만 근거로 OpenAI(`gpt-4o-mini`)에 직접 답변을 생성시킨다 — 에이전트 전체를
   다시 태우지 않는다(검색 품질 자체가 목적이라 생성 단계는 얇게 유지).
3. RAGAS 4개 핵심 지표를 OpenAI를 judge로 계산: `faithfulness`(답변이 검색 문서에
   근거하는가), `answer_relevancy`(답변이 질문과 관련 있는가), `context_precision`/
   `context_recall`(검색된 문서가 정답에 필요한 만큼 정확하고 충분한가).
4. 결과는 기존 `EvalRunRecord`(source="ragas")로 저장돼 다른 평가 결과와 같은 로그에
   쌓인다 — 별도 저장소를 새로 안 만듦.

## 골드셋

`cases/README.md` 참고 — 실제 색인된 문서(2026-07-11 적재분) 10개 기반, 지어낸 질문
아님.

## 웹에서 쓰는 법

Ops "평가" 페이지 → "RAG 품질(RAGAS)" 탭. `OPENAI_API_KEY`가 `ops-server/backend/.env`에
있어야 실행된다(없으면 실행 버튼이 명확한 에러를 냄).

## 추후 고도화 (일부러 지금 안 한 것)

- 골드셋 10개 → 더 다양한 패키지·복합 질문으로 확장.
- 지금은 매번 새 골드셋 전체를 돌린다 — 버전(agent_label) 간 A/B 비교 UI는 아직 없음
  (기존 pm4py/worfbench "버전 비교" 탭과 같은 방식으로 나중에 붙일 수 있음).
- judge 모델 고정(`gpt-4o-mini`) — API(`ExecuteRagasRequest.judge_model`)엔 파라미터로
  이미 있지만, 실행 폼(`evaluation.py`)은 `agent_label`만 입력받고 이 값을 안 보내
  항상 기본값이 쓰인다. 프론트에 선택 UI를 붙이는 건 다음 단계.
- 검색 `mode`(vector/hybrid/hybrid_rerank) 비교는 아직 없음 — 지금은 기본값
  `hybrid_rerank`만 씀. RAG 아키텍처 변경(리랭커 on/off 등)의 효과를 이 골드셋으로
  바로 비교할 수 있다는 게 이 기능의 핵심 가치라, 다음 단계로 자연스럽게 이어짐.
