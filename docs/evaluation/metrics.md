# Metric definitions and reproducibility

The historical `quantitative_report_billgates_20260720T031331Z.json` reports keyword recall 9/10, 16/20 and 23/30 (90%, 80%, 76.7%) for a 35,035-character manuscript. These are observations from one extractor run, not a stable benchmark. Other reports use newly generated keyword pools, so their denominators differ. The historical JSON does not preserve the complete ordered pool or source/manuscript hashes: its 90% cannot be reconstructed from the missing-keyword list. Historical artifacts remain unchanged as provenance.

No verified artifact establishes a general “2% hallucination rate”. Phase 2 flagged-session rate, Phase 4 flag counts, sentence-level unsupported-claim precision, and judge error rate have different denominators. None may be relabeled as hallucination rate. Fact extraction itself is model-dependent even when subsequent string matching is deterministic. G-Eval is an opinion metric and must retain individual runs and failures, not just the best score. 

New quantitative reports require a frozen, reviewed, ordered keyword fixture. Export the exact user-turn text (sessions sorted by ID, turns in stored order), then from `backend` run:

```
.venv/bin/python -m evals.frozen_keywords --source /private/source.txt --output /private/keywords-v1.json
.venv/bin/python -m evals.quantitative_report --email <evaluation-account> --keyword-fixture /private/keywords-v1.json
```

Freezing is an explicit one-time extractor operation and refuses overwrite. Review the candidate pool independently of the generated manuscript before accepting/versioning it. The scoring path never extracts keywords. A changed source hash rejects evaluation; create a new version rather than silently replacing ground truth. Do not commit private interview text. The checked-in synthetic fixture only verifies deterministic regression behavior; it does not support the Bill Gates headline score. Reproducing that score requires the missing original corpus/pool.

Version 2 adds fixture contents/hash, input/manuscript hashes, normalizer version, cutoff configuration, model/reasoning settings, Git revision, Python version, requested/completed judge runs and timestamp. Compare results only when these inputs match. All new quantitative reports follow `quantitative-report.schema.json`; legacy reports are v1 and are not silently rewritten.
