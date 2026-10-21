# Domain boundaries

Backend prompt definitions live in `app/agents/prompts/`: interview, safety, extraction, media, writing, verification, characters and customization. `app/autobiography/` separates repository access, customization, event consolidation, TOC planning, retrieval, writing, verification and publishing. Existing imports through `autobiography_service` and `prompts` remain compatibility exports, not duplicate implementations. Cross-domain functions import dependencies at their call boundary to avoid circular initialization. Tests patch the implementation module when replacing a function.

Readiness thresholds live in `app/autobiography/policy.py` and are included in autobiography responses. Clients consume this policy instead of duplicating numeric constants.

Frontend `features/autobiography` owns API calls, the workspace data hook, an exclusive job reducer, a non-overlapping lightweight polling hook, and independent TOC/chapter/manuscript/photo components. The route is a thin entry point. PDF generation uses a stable object version to detect completion, including regeneration; signed URL rotation does not trigger full manuscript reloads.

`features/interview` owns session state, message rendering and the composer. The dialog mounts per opening, manages focus and traps keyboard navigation; reduced-motion users get instant scrolling. Existing `lib/api` and chat imports are compatibility barrels into feature implementations. API types are domain aliases of generated OpenAPI schemas; TOC and report JSON shapes remain explicit frontend refinements.
