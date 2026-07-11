# 예시 데이터셋 — pm4py / WorFBench 채점 입력 형식

**이 폴더의 파일은 실제 골드셋이 아니라 "이런 형식으로 입력을 준비하면 채점이 된다"를
보여주는 예시 데이터셋이다.** 전체 골드셋(15개 bot)과 채점 스크립트는 별도 리포지토리인
`a360-eval-sandbox`(`Metadata/goldset_pm4py/`, `Metadata/predictions_from_agent_*.json`,
`WorFBench/`)에 있고, 거기서 실제 채점을 돌린 뒤 결과만 `import_sandbox_ab.py`로
이 앱의 평가 로그로 가져온다. 이 폴더는 그 형식을 리포에 같이 남겨서(=GitHub에 커밋),
"pm4py/WorFBench로 채점하려면 뭘 준비해야 하는지"를 이 리포만 보고도 알 수 있게 하는
용도다. 원본은 `RSSFeedReaderBot` bot 하나에서 그대로 뽑은 실제 값이며, 값 자체를
채점에 다시 쓰라는 뜻은 아니다.

## pm4py (`pm4py/`)

pm4py는 "정답 워크플로우(Petri net)"와 "에이전트가 예측한 액션 순서"를 정렬(alignment)해
fitness/precision을 계산한다.

- `RSSFeedReaderBot.pnml` — 정답 워크플로우를 나타내는 Petri net (pm4py가 읽는 형식).
  `.ptml`(process tree) 버전도 같이 만들어질 수 있으나 채점 자체는 `.pnml`만 있으면 된다.
- `predicted_actions_example.json` — 에이전트 예측 결과. 채점에 필요한 필드는
  `source_bot`, `predicted_actions`(각 원소가 `{"package": ..., "action": ...}`) 뿐이고,
  `analysis_steps`/`analysis_ambiguities`/`variables` 등은 에이전트 출력 부가 정보라
  채점 로직이 보진 않지만 `raw`로 같이 보존해두면 나중에 참고하기 좋다.
  `Loop`/`If`/`ErrorHandler` 패키지는 정답 Petri net에는 보이지 않는 제어 구조라서
  채점 전에 걸러진다(`run_pm4py_conformance.py`의 `STRUCTURAL_PACKAGES` 참고).
- `conformance_result_example.json` — 위 둘을 pm4py로 채점한 실제 출력
  (`fitness`, `precision`, `gold_action_count`, `predicted_action_count`). 이 앱에
  기록할 때는 `metrics: [{"name": "pm4py_fitness", "value": ...}, {"name": "pm4py_precision", "value": ...}]`
  형태로 넣고 원본은 `raw`에 그대로 넣으면 된다.

## WorFBench (`worfbench/`)

WorFBench는 "에이전트가 만든 서브태스크 그래프(Node/Edges)"와 "정답 그래프"를 비교해
precision/recall/f1을 계산한다.

- `pred_traj_example.json` — WorFBench 입력 형식 1건. `query.conversations`의
  `assistant` 메시지가 `Node:\n1.<서브태스크>\n...\nEdges:(START,1) ... (n,END)` 형식으로
  그래프를 표현하고, `query.meta.actions`에 각 서브태스크에 대응하는 `{package, action,
  in_catalog}`가 나열된다.
- `eval_result_example.json` — 위 입력을 WorFBench로 채점한 실제 출력
  (`precision`, `recall`, `f1_score`). 이 앱에 기록할 때는
  `metrics: [{"name": "worfbench_precision", ...}, {"name": "worfbench_recall", ...},
  {"name": "worfbench_f1", ...}]`로 넣고 원본은 `raw`에 넣으면 된다.

## 이 예시가 실제로 쓰이는 곳

- 백엔드 `GET /eval/format-guide`가 이 폴더 파일들을 읽어 웹 UI("평가 결과" 페이지 →
  "채점 포맷 안내")에 그대로 보여준다. 파일을 고치면 웹에 보이는 안내도 같이 바뀐다.
