# Development validation

[![CI](https://github.com/tangerineYR/meminisse/actions/workflows/ci.yml/badge.svg)](https://github.com/tangerineYR/meminisse/actions/workflows/ci.yml)

From `backend`, install `requirements/dev.txt`, then run `GATEWAY_BACKEND=mock AUTOBIOGRAPHY_LLM_PROVIDER=solar python -m pytest tests -q`. `requirements/production.txt` excludes evaluation/test-only dependencies; `requirements/evaluation.txt` adds evaluation tooling. The legacy `requirements.txt` remains the full development entry point.

From `frontend`, run `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, and `npm run build` with Node 24. The checked-in lockfile is synchronized. Frontend tests exercise HttpOnly BFF sessions/CSRF, single-flight refresh/retry, unauthorized navigation, TOC selection, review badges, PDF progress, exclusive job state, status deltas and polling cleanup. Fonts are local, with provenance and OFL notices in `docs/licenses`, so builds require no Google Fonts network access.

When backend schemas change, run `python -m scripts.export_openapi` from `backend`, then `npm run generate:types` from `frontend`. Commit both generated files. CI regenerates and fails on drift. Domain type files alias generated schemas and only refine JSON document fields (TOC and validation reports) that the backend currently exposes as unstructured dictionaries.

Real Supabase/AWS/AI integration and migrations against a production database are separate from offline CI. Tests do not claim those integrations work without credentials. See `architecture/security.md` before deploying migration 017 or the BFF.
