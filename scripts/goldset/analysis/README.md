# Goldset Analysis

`analysis/` is for read-only corpus investigation and candidate selection. Scripts here
may read `dataset/`, RAG exports, and processing reports, but they should not mutate
the curated workflow files.

Use this folder for:

- checking RAG action coverage
- summarizing action counts
- investigating custom JAR usage
- selecting or explaining eval candidates
- documenting experiment findings

Do not put live backend calls, PM4Py/WorFBench scoring orchestration, or artifact
generation here. Those belong in `runner/`, `evaluation/`, and `processing/`
respectively.
