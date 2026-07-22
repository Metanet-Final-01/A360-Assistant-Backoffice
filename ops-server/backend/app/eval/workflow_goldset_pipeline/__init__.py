"""zip 업로드 -> canonical 변환 -> pm4py/WorFBench 변환까지의 골든데이터셋 생성 파이프라인.

기존에 scripts/agent_flow_eval/(다른 워크트리에만 있던 CLI 스크립트들)이 하던 일을
웹에서 파일 하나씩 돌려볼 수 있게 옮겨온 것이다. 핵심 변환 로직(canonical_convert,
pm4py_convert, worfbench_convert)은 그 스크립트들의 로직을 그대로 옮긴 것이고,
새로 짠 부분은 웹 실행(zip_extract, workflow_extract, pipeline_runner)뿐이다.
"""
