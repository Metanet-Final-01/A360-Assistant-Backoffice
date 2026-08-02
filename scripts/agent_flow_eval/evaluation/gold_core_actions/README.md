# Diagnostic annotations only

These UID-level files are retained as analysis notes. They are not applied by official batch or final-goldset scoring.

Reasons such as `internal_state_assignment`, `path_assembly_detail`, and `duplicate_operation_in_mutually_exclusive_branch` depend on a particular Gold implementation. Applying them only to Gold would make the denominator case-dependent and could raise scores without a symmetric rule.

Official evaluation uses `action_filters.normalize_steps_for_evaluation()` while converting both Gold and prediction workflows. A new exclusion is accepted only when it can be expressed as the same deterministic rule for both inputs and is supported independently of observed scores.

`_llm_classification_cache.json` in this same folder belongs to a **different, unrelated
system**: `action_matching.classify_core_business_actions()` (2026-08-03), which applies
symmetrically to both Gold and prediction actions via a shared rule+LLM classifier, not
a Gold-only UID list. See `../README.md`'s "Core-business classification" section for
the full design and validation against these historical files.
