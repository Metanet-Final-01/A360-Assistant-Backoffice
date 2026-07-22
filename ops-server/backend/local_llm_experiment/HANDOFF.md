# 인수인계 (2026-07-23)

- 로컬 LLM(EXAONE-4.5/4.0, `http://192.168.1.147:8820/v1`)로 RAG 생성/RAGAS 채점 비교 중.
- 서버는 한 번에 모델 하나만 서빙됨 — 시작 전 `curl http://192.168.1.147:8820/v1/models`로 어떤 모델이 떠있는지 먼저 확인.
- 로컬 judge는 반드시 `extra_body={"chat_template_kwargs": {"enable_thinking": false}}`로 reasoning 꺼야 동작함(안 끄면 max_tokens 다 쓰고 실패).
- reasoning-ON은 4.5/4.0 둘 다 실측으로 실패율 높음(각각 근거는 `RAGAS_JUDGE_MODEL_COMPARISON_2026-07-22.xlsx`의 "추론켬_제외사유" 시트, 로컬엔 `docs/local/gpt_handoff_2026-07-22/`에 있으나 gitignore라 이 브랜치엔 없음 — 필요하면 원본 컴퓨터에서 다시 받아야 함).
- 스크립트는 `ops-server/backend/local_llm_experiment/`에 있고, `cd ops-server/backend`에서 실행해야 함(상대 import 기준).
- `build_generation_comparison.py`, `build_judge_comparison_xlsx.py`의 `OUTPUT_PATH`는 원래 컴퓨터의 임시 스크래치패드 절대경로라 이 컴퓨터에 맞게 고쳐야 함.
- 다음 할 일: gpt-4o-mini 생성 vs 로컬 생성 비교, judge 4조건 비교 다 끝남 — 사용자에게 다음 방향(전체 129케이스로 확대할지, chunk_size 다른 조합도 로컬모델로 돌릴지 등) 확인 필요.
- 실험용 임베딩 테이블(`rag_documents_eval_*`)은 git으로 안 옮겨져서 원격 Neon(`RAG_DATABASE_URL`)에 `rag_documents_eval_cs1200_ov0`만 복사해뒀음(9570건) — Neon 프로젝트 용량 한도가 512MB라 13개 전부는 못 옮김. 다른 chunk_size 조합 필요하면 원격에 올리지 말고 `scripts/ragas_eval/chunk_candidates/build_candidate.py --chunk-size N --overlap M`으로 그 컴퓨터 로컬 DB에 직접 재생성할 것(원본 문서+OpenAI 임베딩으로 100% 재현 가능).
