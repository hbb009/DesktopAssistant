# Long task checkpoints implementation plan

Goal: retain small durable partial results and resume long Agent tasks without repeating completed work.

Architecture: keep the existing immutable per-step commits and hash validation. Add 25-step checkpoint journals for every workflow, 25-chapter catalog manifests, deterministic explicit chapter-range selection and immediate web result projection. Keep fiction approval gates and existing source/output protection.

1. Add chapter-range parser and checkpoint journal module.
2. Remove web catalog's 500-link rejection; retain fiction count limits, avoid applying fiction defaults to web tasks.
3. Save checkpoints on committed and reused steps. Publish web chapters immediately, preserve prior outputs and user modifications.
4. Display the current saved checkpoint in task progress.
5. Test a 601-chapter mock catalog, 1–300 selection, pause/resume, retries, final completion and modified output protection. No real network or model calls.

Limits: these records operate at existing engine step boundaries. An interrupted uncommitted model step is retried; token-level generation continuation is not provided. Catalog steps are still kept in the main plan, while chapter manifests and execution records are split on disk.
