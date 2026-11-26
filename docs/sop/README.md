# Build and check the Chinese SOP

This directory contains documentation tooling only. It installs no application dependencies.

On macOS with Swift, AppKit, CoreText, PDFKit and PingFang SC:

```sh
mkdir -p runtime/sop-qa
swift -module-cache-path runtime/sop-module-cache docs/sop/build_sop.swift docs/MemorySearch-SOP-zh-CN.md docs/MemorySearch-SOP-zh-CN.pdf runtime/sop-qa
```

The builder requires nine deliberate pages, checks layout overflow, extracts required text and verifies embedded fonts. It normalizes metadata and renders every page at 2× to the ignored QA directory. Inspect every PNG for Chinese glyphs, readable type, page numbering and clipping. Commit only the Markdown, builder, README and final PDF; never the PNGs or extracted QA files.

Public documents use `<CODE_CHECKOUT>`, `<MEDIA_ROOT>` and `<INDEX_ROOT>`. Machine-specific paths, current private counts, queries, screenshots, credentials and local migration records do not belong here. The diagrams are drawn from code and contain no media screenshots.
