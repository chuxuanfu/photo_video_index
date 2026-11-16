# Project rules

- Private media, indices, transcripts, captions, file lists, EXIF/GPS, local configuration and logs stay local. Never include them in commits, issues, PR descriptions or external requests.
- Scan only a root explicitly selected by the user. Never discover media by scanning a home, whole disk, Photos database, network share or another computer.
- Keep original media read-only. Preserve existing indices and user changes. Derived files belong under ignored `.local/`.
- Public Git content is source, install/dependency manifests, generic templates, documentation and explicitly synthetic fixtures. Models, binaries, environments and caches are downloaded on the user's machine.
- Use MLX-VLM vision and the pinned visual checkpoint. No cloud or text-only fallback. Runtime network is loopback only; do not weaken the process sandbox or system security.
- Do not start full-library analysis unless explicitly requested and the supervised release quality gate has passed. Keep failed items distinct from successful analysis.
- Search UI, RAG and semantic search are outside the current development scope. Concentrate on accurate portable index evidence.
- Before publishing, inspect the exact staged files and run the public-content audit. Do not stage ignored runtime data or blanket-add the worktree.
