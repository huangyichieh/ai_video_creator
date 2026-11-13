# Refactor status and verification

Updated: 2026-11-12. Read this together with the root README; earlier dated specifications describe the production engines, not the new client/server layout.

## Inventory and relocation

The original repository had a Streamlit application, 235 Python package modules, file-backed project services and 205 test files. It had no Flutter source, HTTP server or SQLite database. The selected migration approach was native Flutter screens.

- `frontend/`: new Flutter Web client with GetX routing/state, generated JSON model serializers, feature folders and shared controls.
- `backend/routers/`: project/workspace, jobs, uploads/media and credential HTTP endpoints.
- `backend/services/story_channel/`: reused production engines. None of the original 235 modules was discarded.
- `backend/models/`: canonical Pydantic models; old import locations re-export the same classes to retain identity and existing integrations.
- `backend/workers/`: durable job queue and cancellation. Provider limits remain inside the existing generation services.
- `backend/legacy_ui/`: 86 original UI modules isolated for compatibility and regression tests. The native client does not embed or require a Streamlit server. The FastAPI workspace path imports no Streamlit.
- `storage/outputs/AI_Story_Channel/`: relocated original project bundle, preserving its internal directory structure and history.
- `storage/database/jobs.sqlite3`: new job tracking database. There was no pre-existing SQLite database to convert.
- `storage/uploads/` and `storage/temp/`: incoming uploads, cached UI thumbnails, browser proxies and transient work.

At the initial structural relocation, the project bundle contained **2,433 files / 1,477,965,349 bytes** and SHA-256 hashes matched after the move. That historical inventory is local at `storage/database/storage-migration.json`, excluded from Git. Historical absolute media references resolve through a compatibility path adapter; manifests were not rewritten.

The subsequent project-document migration transferred **30 Markdown files** to JSON documents or verified JSON archives. The audit covered **2,422 original files** and confirmed that all non-Markdown files were unchanged and every original Markdown byte was recoverable from its archive. All **1,064 resulting project JSON files** parsed successfully; no Markdown remained in the live project bundle. The local audit is `storage/database/markdown-archive/audit-cd833d45c496409d93a759502a0e07cc.json`.

Outlines now use `outline.json` with a `text` field. Histories use JSON; existing structured story/script/review/voice data remain authoritative. Old BOMs and newlines are preserved in the archive; canonical text matches the old reader's newline handling so fingerprints and approvals remain valid. Startup migrates older project bundles with the same archive-before-removal checks. Markdown is rendered on demand for export, while repository documentation remains Markdown.

## Runtime behavior

The native client covers World, Assets, Story/Episode, Animations, Final and Analytics. It includes original/suggested review decisions, per-character Visual/Voice/Costume tabs, reusable media, episode selection, timed scene images, saved revisions, scene-video region/time edits, editable voice lines, jobs, masked credentials and provider preferences. Advanced operations use a searchable action catalog and structured parameter forms.

HTTP routers validate request envelopes. Services adapt arguments to the existing domain engines and capture current source/version guards. Workers persist queued/running/succeeded/failed/stopped/interrupted results. Restarted tasks are marked interrupted, rather than automatically repeating potentially billable requests. Related project mutations serialize; independent sample/design/audio operations use the engines' existing concurrency controls.

Polling returns small job summaries; full results and sanitized provider error reports load on demand. UI images use cached thumbnails, with original-resolution viewing and downloads. Drafts and tab expansion use persistent route/controller state instead of full-page Streamlit reruns.

API keys stay in `.env` or `STORY_ENV_FILE`; browser responses contain only masked previews. Changes wait for active jobs. Existing environment values take precedence at startup. Storage and credentials are ignored by Git. Stop the previous app process before switching startup commands so an already-running instance cannot keep an old storage configuration.

## Verification

- Flutter analyzer: no issues.
- Flutter model/navigation/transport tests: passed (3 tests).
- Flutter release Web build: passed.
- FastAPI and Flutter development servers: started successfully on isolated loopback ports.
- Compiled client: rendered existing projects and loaded all six workspace pages in a browser without JavaScript errors.
- Python dependency check and backend compilation: passed.
- Latest focused checks: **38 passed** across migration contracts, foundation, project deletion and generation preferences. A subsequent compatibility-UI/foundation/migration subset also passed (**32 tests**).
- Native API contracts include offline World review/approval, episode creation, inline scene editing with retained approval, outline review/approval, revision access, saved visual prompts, upload boundaries, key masking, durable jobs and stop/restart behavior. No paid generation was performed for verification.

The full run completed **1,198 tests: 1,085 passed, 107 failed, 6 setup errors**. Every non-passing case was replayed against the original committed version: **112 reproduced there**. The remaining assertion expected an unresolved relative storage path; it was updated for the requested project-root-relative behavior and now passes. Full-suite results therefore remain non-green due to inherited cases, including removed UI controls, earlier background workflows/provider expectations and a Windows maximum-path failure. Local XML/log reports and baseline comparison results are in ignored `storage/temp/`.

## Remaining acceptance work

This is a substantial native UI port. Specialized workflows sometimes use structured forms in place of previous dedicated Streamlit controls. A complete interaction-by-interaction parity review is still needed; successful compilation does not establish UI equivalence. Real-provider end-to-end generation and final rendering were not performed with the user's paid credentials. The inherited test failures need their own current-design audit before the entire suite can be considered a release gate.

The compatibility UI and import namespace remain deliberately available while those checks are completed. They are not the native client's implementation. No commit or push was created for this refactor.
