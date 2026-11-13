# Local work and publication

- Edit, run `tools/run-checks.py`, review every changed line, then make a local commit.
- Every independent verified change should have its own local commit. Include `索引:`, `验证:`, and `范围/限制:` in the commit body.
- Push only when the user explicitly requests it. Do not automatically pull, merge, or rewrite remote history.
- Before every push, review complete outgoing history, filenames, comments, metadata, hidden files and new identifiers. Check credentials, subscription links, node/server names and private IP-to-device mappings. Run `tools/privacy.py --history`; the local pre-push hook enforces the automated part. Pattern scanning does not replace reading changes.
- Keep tools, GEO data, provider caches, real configurations, business-test notes and recovery snapshots outside published files. `.local/` is ignored. Never publish snapshots as RULESETs.
- Do not restart, reload, or deploy to the router without a separate deployment request. Before deployment, reject zero Netflix candidates and unexpected empty pools, ignore old saved choices for first activation, then inspect and migrate the catchall selection and pin a healthy AI node. See `tools/README.md`.
- Comments added to the template belong on their own lines, with blank lines between sections.
