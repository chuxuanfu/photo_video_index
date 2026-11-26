# MemorySearch local installation and maintenance

The current application is `memorysearch_local/`, launched by the root `MemorySearch-*.command` files. The pre-existing repository workflow and its files remain intact. Do not run its older setup/import commands to prepare this application or point its default index at an existing library.

## Storage contract

- Code: one checkout of this repository. The chosen release branch is separate from `main` until reviewed/merged.
- Runtime: ignored `runtime/` inside that checkout; local Python, model weights, native binaries, scratch and configuration stay here.
- Originals: an explicit local `MEDIA_ROOT`, read-only during search and maintenance.
- Persistent index: the direct child `MEDIA_ROOT/.memorysearch-index`, configured separately as `INDEX_ROOT`. Exclude it from media discovery. Do not overwrite any older root `index.json`.
- No production index is created on ordinary startup. A verified existing SQLite snapshot and `READY.json` are required.

Configuration is owner-readable `runtime/local-config.json` (0600). Copy the example, replace placeholders locally and restrict permissions before running. The config accepts `SOURCE_INDEX_ROOT` for the one-time source snapshot. Do not commit this field's value. The source must be an existing completed compatible index, never an arbitrary folder selected by guessing.

## Runtime preparation

Target: Apple Silicon macOS with Xcode command-line tools, system `sandbox-exec`, Swift/AppKit/AVFoundation, and Python 3.12. The packaged installation was exercised with Python 3.12.13. Runtime versions are recorded in `memorysearch_local/runtime-versions.lock.json`; all installed package versions are pinned in `requirements-runtime.lock`.

Create a dedicated local environment; never install into system Python:

```sh
mkdir -p runtime
chmod 700 runtime
python3.12 -m venv runtime/python
runtime/python/bin/python -m pip install -r memorysearch_local/requirements-runtime.lock
```

This command is a reproducibility recipe, not a statement that a clean online reinstall was tested. The delivered local environment was cloned from the already working dedicated environment, then its relocated prefix, imports and actual model inference were tested. The macOS system SDK and the environment's base Python installation remain external prerequisites. Keep their installation available.

### Official model files

No weights or converted/community model files are published in Git. Obtain only the official revisions and the exact file lists/hashes in:

- `EMBEDDING-MODEL-LOCK.json`: `google/embeddinggemma-2`, revision `914f7f89142e33e77833254d9c9b90c3cef7303b`, Apache-2.0; read the model card and referenced use policy.
- `VERIFIER-MODEL-LOCK.json`: the existing `Qwen/Qwen3-VL-4B-Instruct`, official fixed revision, Apache-2.0. This is the frozen photo presentation reviewer, not a new video verifier.

Official sources: [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2), [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct). Keep preparation downloads separate from media-processing processes and deny them access to originals/config/index. All runtime model loading is local-only, safetensors-only, with remote code disabled.

Place the verified files at `runtime/models/embeddinggemma-2` and `runtime/models/verifier-4b`. The embedding processor's `chat_template.jinja` and `tokenizer.model` are necessary; omitting them causes actual encoding to fail even if model loading succeeds. Both are now included in the fixed manifest.

### Native tools

Provide FFmpeg and FFprobe at `runtime/native/ffmpeg` and `runtime/native/ffprobe`. The local delivered builds are recorded by version and SHA256 in `native-tools.lock.json`. They are separate executables, not Git assets. Licensing/provenance is recorded in `THIRD-PARTY.md`. An alternative system installation requires an explicit tested local configuration, not silent binary replacement.

`MemorySearch-Install.command` verifies model hashes, Python imports and tool execution and builds the two native Swift executables. It does not download, start indexing or change production. Verify all prerequisites before migrating.

## No-sync gate and transactional migration

`MemorySearch-Check-Storage.command` checks local/internal mount, symlink/alias and cloud placeholder/marker metadata. It uses `FileManager.isUbiquitousItem(at:)` for iCloud targeting. Missing optional URL attributes do not prove absence of sync. General third-party sync still requires a factual local confirmation; `NO_SYNC_CONFIRMED` starts false and must never be used to bypass an unknown result.

If any gate is unresolved, do not write a private index. Complete synthetic tests and keep the previous service. Do not disable global iCloud/network settings.

`MemorySearch-Migrate-Index.command` refuses an existing destination. Its independent sandbox denies all networking and original-media reads. SQLite's backup API takes a consistent transaction snapshot including active WAL state, checks schema/profile/vector lengths/foreign keys and before/after counts, and copies only referenced whole-asset thumbnails. Current UI routes use these whole-asset thumbnails; no window thumbnails or other source files are required. The source database is preserved. A failed new destination is removed; source and root `index.json` are never removed or edited.

## Daily operation and incremental maintenance

Use Open, Status and Stop from this checkout. Port ownership is bound to a per-instance nonce. A foreign listener is never stopped. Startup/maintenance serialize on a controller lock; the model is resident within a single owned worker. The app and client accept only the configured loopback endpoint and are independently denied nonlocal connections.

Photo review preserves the native Top 10 members, displays original candidates first, then groups supported items. Unknown/refused candidates remain expandable. Failure/timeout restores original order. Native video retrieval and ranking are unchanged. These presentation guarantees do not establish perfect accuracy.

`MemorySearch-Incremental.command` is metadata-only by default. With the service stopped, `--apply` explicitly processes new/changed files through the existing encoder, skips unchanged files, marks missing items without deleting originals and keeps one transactional pre-update snapshot under the index. The same fixed index profile must already exist; no schema migration, rekey or full-rebuild shortcut is invoked. A busy/foreign port blocks apply. Actual private incremental execution is separate from synthetic validation.

To disable photo presentation review operationally, run `MemorySearch-Rollback-Photo.command`; native candidates and the index are preserved. To restore a previous deployment, stop only the new owned service and use the recorded, verified previous launcher/index pair. Do not point an incompatible older program at the new index. Original data/runtime stay available until the owner accepts cutover.

## Verification and limits

```sh
./MemorySearch-Verify.command
./MemorySearch-Verify.command --live
```

The first command runs no-inference boundary tests. The second creates its own generated fixtures under ignored `runtime/test-runs`, uses a distinct loopback port and CPU, exercises real retrieval and the native AppKit/AVPlayer entry, verifies outbound denial, dry run, transactional copy, stop, and no-change incremental apply. It never accepts a production media root. Each run writes an aggregate `SUMMARY.json` locally and exits 0 only on pass.

The live smoke disables the photo visual model to avoid overlapping the user's GPU workload. Member/group/fallback behavior and the Transformers feature-signature repair have independent contract tests; actual relocated photo-model inference and private library acceptance must be reported separately, not inferred from this smoke. Timings are individual CPU integration observations, not UI p95 or an accuracy benchmark.

## Public-release audit

Use explicit file staging only. Check the complete staged diff and generated PDF text, metadata, fonts and every rendered page. Runtime/config/index/media/log/model files remain ignored, including tests generated at runtime. Never use force-add or force-push. Preserve remote main and all pre-existing files; push only the reviewed isolated release branch.
