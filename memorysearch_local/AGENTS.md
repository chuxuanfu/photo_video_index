# MemorySearch local application scope

This directory packages the existing resident EmbeddingGemma 2 search and photo presentation review requested by the owner. The root repository's older MLX index workflow is retained separately; do not replace the fixed Gemma model or enable that older workflow from these launchers.

All root privacy rules apply. Public source must contain no machine paths, local configuration, user queries, media, indices, logs, binaries, environments, model weights or caches. Runtime belongs in ignored `runtime/`; the private index belongs only in an explicitly configured, verified local `INDEX_ROOT`. Unknown sync status blocks private migration. Never infer that an absent cloud attribute proves no synchronization.

Keep original requested-page membership (1–50, default 10) and native video ordering. Photo review groups supported candidates first and preserves all other candidates; timeout/failure returns native ordering. Do not rebuild a completed production index on startup. Incremental work is explicit and dry-run by default, with the index directory excluded from discovery.
