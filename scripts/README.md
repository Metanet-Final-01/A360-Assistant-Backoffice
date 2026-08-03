# Evaluation Scripts

This directory has two evaluation workspaces with separate responsibilities.

```text
agent_flow_eval/  Agent flowchart evaluation against curated A360 workflow gold artifacts.
ragas_eval/       RAGAS-style retrieval/answer quality evaluation for the RAG pipeline.
```

Keep generated datasets, logs, and reports out of git. Commit source scripts, rules,
and short documentation only.

## Boundaries

- Use `agent_flow_eval/` for gold workflow extraction, backend runner calls, PM4Py,
  WorFBench, canonical action rules, and core-task projection.
- Use `ragas_eval/` for retrieval/grounding/answer metrics over RAG queries and
  expected references.
- Do not mix RAGAS reports into the agent-flow evaluation report tree.
