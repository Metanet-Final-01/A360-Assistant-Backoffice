# 세션 인수인계 (작성: 2026-07-23, 이 세션이 너무 길어져서 다음 세션/컴퓨터로 넘기는 용도)

## 0. 가장 먼저 볼 것 — 브랜치 상태 경고 (중요, 미해결)

이 브랜치(`wip/local-llm-test`)는 `feat/ragas-rag-eval`에서 갈라져 나왔는데, 그 `feat/ragas-rag-eval`은
Codex가 작업 중인 `codex/latest-ops-dev` 브랜치와 **57개 커밋만큼 갈라져 있음** (observability
관련 PR들 다수 — RPA-255, RPA-256 등 codex/latest-ops-dev에는 있고 이 브랜치엔 없음).
반대로 이 브랜치엔 codex 쪽에 없는 커밋이 3개 있음(RAGAS 골드셋 작성 UI, e5f0cb8/1c0a3e6/d23b298).

**즉 이 브랜치는 최신 backend 코드 기준이 아니라 좀 오래된 스냅샷 위에 로컬 LLM 실험을 얹은 것.**
다른 컴퓨터에서 이어서 진짜 작업(배포용, PR용)을 하려면 이 상태로 그냥 쓰면 안 되고,
사용자한테 먼저 물어봐서 (a) codex/latest-ops-dev 위로 리베이스하거나 (b) RAGAS 실험 관련
파일만 골라서 최신 브랜치에 옮기는 방법을 정해야 함. 자동으로 리베이스/머지 시도하지 말 것.

worktree 목록(이 컴퓨터 기준):
- `A360-Assistant-Ops` (여기, wip/local-llm-test)
- `A360-Assistant-Ops-latest` → codex/latest-ops-dev (Codex가 건드리는 최신 브랜치)
- `A360-Assistant-Ops-dev-merge`, `-pr22`, `-pr39-reviewfix`, `-rpa150`, `-rpa187` (다른 용도)

## 1. 이번 세션이 뭘 했는지 (한 줄 요약)

RAGAS chunk_size/overlap 실험을 char 기반으로 끝낸 뒤, 로컬에 있는 llama-server(EXAONE
모델)를 발견해서 "생성/채점 둘 다 무제한 실험 가능"이라는 아이디어로 로컬 LLM 파이프라인을
새로 만들고 비교 실험을 돌림. 그 다음 "다른 컴퓨터에서 이어하기" 위한 git/DB 이전 작업을 함.
마지막으로 교수님 지시로 토큰 기반 청킹 후보 11개를 미리 만들어두는 중(진행 중, 안 끝남).

## 2. 로컬 LLM 실험 — 확정된 결론

- 서버: `http://192.168.1.147:8820/v1` (llama-server, OpenAI 호환). **한 번에 모델 1개만 서빙됨**
  — 작업 전 `curl http://192.168.1.147:8820/v1/models`로 확인 필수.
- **로컬모델을 judge로 쓸 때 reasoning은 반드시 꺼야 함**
  (`extra_body={"chat_template_kwargs": {"enable_thinking": false}}`) — 안 끄면 max_tokens를
  reasoning이 다 먹어서 RAGAS가 요구하는 최종 답을 못 냄(`LLMDidNotFinishException`).
- **reasoning-ON은 EXAONE-4.5, 4.0 둘 다 실패율 높음** (4.5는 사실상 100%, 4.0은 실측 60%,
  케이스당 4~5분으로 매우 느림) — 재시도할 가치 없다고 판단, 정식 10건 실행은 안 함.
- 생성모델 비교(같은 judge=gpt-4o-mini로 고정): **gpt-4o-mini 생성이 로컬(EXAONE-4.5) 생성보다
  answer_correctness가 뚜렷하게 높음**(0.678 vs 0.317). 로컬 생성은 무료([$0]무제한 실험)라는
  장점은 있지만 품질은 gpt-4o-mini가 낫다는 게 실측 결론.
- judge 비교(생성모델 EXAONE-4.5로 고정, judge만 바꿈): 4.5-judge(추론끔) 0.349 / 4.0-judge
  (추론끔) 0.340 / gpt-4o-mini-judge 0.317 (answer_correctness). Faithfulness는 gpt-4o-mini가
  더 높게 줌(0.82 vs 0.59~0.67). **로컬 judge는 gpt-4o-mini보다 실패율도 높고(4.0도 48/50) 점수도
  갈려서 "최종 판단"용으로는 비추천 — 생성만 로컬로, 채점은 gpt-4o-mini로 하는 걸 권장**.
- **중요한 미해결 gap**: 이 판단들 중 대부분은 RAGAS가 계산한 최종 숫자 점수(`eval_runs.jsonl`)에는
  근거하지만, **judge가 실제로 뭘 근거로 그 점수를 줬는지(원문 reasoning/verdict)는 4개 조건 중
  2개만 남아있음** — `data/judge_raw_cs1200_gen_gpt4omini.jsonl`(gen=gpt4o-mini/judge=gpt4o-mini),
  `data/judge_raw_cs1200_exaone40_reason_smoketest.jsonl`(4.0 추론켬 스모크, 부분). 4.5-off/4.0-off/
  4.5-on 세 조건은 원문 없음(캡처 기능을 나중에 만들어서). 사용자가 이 판단 근거를 GPT에게
  직접 검증받고 싶어함 — 로컬 서버 다시 붙으면 `_JudgeCaptureHandler`(run_local_model_combo.py
  안에 있음, reasoning on/off 상관없이 캡처 가능 확인됨) 켜고 재실행해서 채워야 함.
- 엑셀 리포트(gitignore돼서 이 브랜치엔 없음, 로컬에만 있음):
  `docs/local/gpt_handoff_2026-07-22/RAGAS_JUDGE_MODEL_COMPARISON_2026-07-22.xlsx` — 요약/
  케이스별_상세/답변원문/추론켬_제외사유 4개 시트. **판단원문은 이 엑셀에도 없음** (숫자 점수와
  생성답변 텍스트만 있음, 확인 완료).

## 3. 다른 컴퓨터로 이어하기 위해 옮겨둔 것들

- **git**: `wip/local-llm-test` 브랜치 (원격 push 완료). 코드는 다 있지만 위 0번 경고 참고.
- **DB 덤프**(git으로 안 옮겨짐, 사용자가 구글드라이브로 직접 업/다운로드 예정):
  - `docs/local/db_dumps/rag_documents_eval_all_2026-07-23.dump` (1.1GB, pg_dump custom format,
    글자기반 청크 후보 13개 테이블 전부 — cs300/600/900/1200/1500 각 overlap 조합)
  - `docs/local/db_dumps/ragas_eval_data_2026-07-23.zip` (1.8MB, eval_runs.jsonl +
    chunk_experiment_embed_cache.json + judge_raw_*.jsonl 2개 등 — RAGAS 실험 이력만 추림)
  - 복원 방법: `docker cp <dump> a360-postgres:/tmp/x.dump && docker exec a360-postgres pg_restore
    -U a360_admin -d a360 --no-owner /tmp/x.dump` (테이블), zip은 풀어서 `ops-server/backend/data/`에.
- **원격 Neon(RAG_DATABASE_URL)**: `rag_documents_eval_cs1200_ov0`(9570건)만 복사해둠 — Neon
  프로젝트 용량 한도(512MB)라 13개 전부는 못 옮김, 나머지는 필요시
  `scripts/ragas_eval/chunk_candidates/build_candidate.py --chunk-size N --overlap M`으로 재생성.

## 4. 지금 진행 중인 작업 (끝나기 전에 세션/컴퓨터가 바뀔 수 있음)

교수님이 토큰 기반 청킹도 꼭 테스트해보라고 해서, 토큰수 기준 후보 11개
(128,150,256,300,512,600,900,1024,1200,1500,2048 / overlap=0)를 로컬 DB
(`rag_documents_eval_tok{N}_ov0` 테이블, port 5433)에 순차로 빌드 중.

- 스크립트: `scripts/ragas_eval/chunk_candidates/build_candidate_token.py` +
  `build_all_token_candidates.py` (이 브랜치에 커밋되어 있음, `python build_all_token_candidates.py`로 실행)
- **2026-07-23 기준 진행상황**: `rag_documents_eval_tok128_ov0` 하나만 진행 중(43,000건+, 아직
  안 끝남 — 가장 작은 token_size라 청크 수가 제일 많아서 제일 오래 걸림). 나머지 10개는 시작도 안 함.
- 자식 프로세스 stdout 버퍼링 때문에 로그 파일(`build_all_token_candidates_v2.log`)엔 거의 안
  찍힘 — **로그 믿지 말고 DB row count 직접 조회해서 진행상황 확인할 것** (이 세션에서 사용자가
  지적한 부분, "중간점검 조용히 넘어가지 말라"는 피드백 있었음).
- 예상 임베딩 비용: 코퍼스 전체 4.55M 토큰 기준 후보 1개당 ~$0.09, 11개 합쳐도 ~$1 수준.

## 5. 다음 세션 체크리스트

1. 이 문서 0번(브랜치 divergence) 먼저 사용자한테 확인 — 리베이스할지 말지 임의로 정하지 말 것.
2. 토큰 후보 빌드가 끝났는지 DB row count로 확인 (`SELECT count(*) FROM rag_documents_eval_tok{N}_ov0`).
   안 끝났으면 이어서 돌리거나(같은 스크립트, 이미 된 건 skip 로직 있음) 계속 대기.
3. judge 판단원문 gap 채우기 필요한지 사용자한테 확인 (로컬 서버 접속 가능해야 함).
4. 글자기반(5개) vs 토큰기반(11개) 후보 전체를 어떤 순서/기준으로 좁혀갈지(Vector→Hybrid 단계별
   스크리닝 패턴, 이전 chunk_size 결정 때 쓴 방식) 사용자와 다시 정렬.
