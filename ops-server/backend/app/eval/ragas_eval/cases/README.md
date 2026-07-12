# RAG 검색 품질 골드셋 (RAGAS)

`rag_goldset_v1.json`은 실제 색인된 RAG 문서(`rag-server`가 적재한 A360 패키지/액션
문서, 2026-07-11 적재분)에서 뽑은 10개 케이스다. 지어낸 질문이 아니라, 실제
`rag_documents.jsonl`에 있는 문서의 "설명/파라미터/리턴" 필드를 그대로 근거로 질문·
정답을 만들었다 — 검증 안 된 "공식 문서 설명" 프리텍스트(크롤링 매칭 노이즈가 섞여
있는 걸 확인함)는 정답 근거로 쓰지 않았다.

## 필드

- `case_id` — 케이스 고유 id.
- `question` — 사용자가 물어볼 법한 자연어 질문.
- `ground_truth` — 사람이 검증한 정답 요약(RAGAS의 `context_recall`/`context_precision`
  계산에 참조로 쓰임 — `answer_relevancy`/`faithfulness`는 참조 없이 질문·답변·검색된
  문서만으로 계산되므로 여기 안 쓰인다).
- `reference_doc_ids` — 참고용(이상적으로 검색돼야 할 문서 id). RAGAS 지표 계산에는
  안 쓰인다(LLM judge가 검색된 문서의 관련성을 직접 판단하는 방식이라) — 사람이 결과를
  검토할 때 "실제로 이 문서가 검색됐는지" 대조하는 용도.

## 왜 10개뿐인가

첫 버전(v1)이라 파이프라인이 실제로 동작하는지 검증하는 목적이 크다. 케이스를 늘리는
건 "추후 고도화" 범위 — 패키지 다양성을 넓히거나(현재는 Excel/Email/File/Delay/
Datetime/Dictionary/Browser 7개 패키지), 복합 질문(여러 액션을 조합해야 답이 나오는
질문)을 추가하는 식으로 확장할 수 있다.
