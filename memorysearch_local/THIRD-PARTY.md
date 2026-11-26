# Third-party provenance

`vendor/mediaindex` is a locally adapted backend snapshot packaged for the current MemorySearch application. Upstream project: [harsheetamorey/mediaindex](https://github.com/harsheetamorey/mediaindex), version 1.1.1 reference tag `v1.1.1`, commit `33c6343471d1984e876c42cf82371d4c8b14c711`. This packaging is not claimed to be a byte-identical upstream release. The local snapshot includes compatibility, Live Photo, HDR and file-level retrieval adaptations inherited from the working application. `vendor/source-manifest.json` records the exact distributed file hashes. No upstream or downloaded script is executed during provenance checks.

Source is Apache-2.0. Full upstream license and notices are retained in `vendor/LICENSE-MediaIndex.txt` and `vendor/NOTICE-MediaIndex.md`, fetched at the fixed commit. Optional capabilities mentioned in the upstream notice are not exposed by the MemorySearch launchers, and their weights are not included.

The small bundled place-name resource is a GeoNames derivative (cities15000), © GeoNames, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribution and reduction details are retained in `vendor/THIRD_PARTY_DATA-MediaIndex.md`. It is generic licensed upstream data, never a user location or media index.

The resident official Gemma encoder and frozen Qwen photo reviewer are separate local model downloads, not redistributed here. Their official publisher, revision, license and expected SHA256 values are in the two model manifests. Model loading disables remote code and network access.

FFmpeg/FFprobe are local external processes, not committed. The inherited local Apple Silicon executables identify FFmpeg/FFprobe 8.1.2; their license command was checked and reports GPL version 3 or later. Their binary publisher is not independently established, so no publisher attribution or redistribution right is inferred. Official source: [FFmpeg source releases](https://ffmpeg.org/releases/). Keep the local binary supplier's license/source notices with any binary redistribution; this repository distributes no binary. The native tool manifest records the exact locally exercised bytes.

Python package versions are recorded, not vendored wheels. Their upstream license notices remain with installed distributions in ignored runtime. Swift/AppKit/AVFoundation/PDFKit are macOS system prerequisites, not bundled copies.
