# Bundled component notices

This collection retains pinned upstream notices for the Linux payload's Ghostty, zmx, fonts and Swift runtime inputs.
It records source identities and coverage limits; it is not blanket legal or release clearance.
`sources.json` lists every retained file and SHA256.
`inventory.json` maps source archives, component notices, the 16 reviewed runtime filenames and the 602 theme files.
Both documents are shipped beside these notices at `share/licenses/agterm/`.

## Component inventory

| Payload association | Retained sources |
| --- | --- |
| `libghostty.so` | Ghostty `0ba6250388641f52135414b38c4259aa682c489b`, plus the exact dependency manifests used below |
| Ghostty Zig inputs | libxev, vaxis, z2d (including MPL and bundled third-party notices), uucode (including Unicode and Hoehrmann notices), zf |
| Ghostty C/C++ inputs | FreeType 2.13.2, Fontconfig 2.14.2, HarfBuzz 11.0.0, libpng 1.6.43, zlib 1.3.1, libxml2 2.11.5, Oniguruma 6.9.9, glslang 14.2.0, SPIRV-Cross, Highway, Wuffs, Dear ImGui 1.92.5 and Dear Bindings 0.17 |
| Vendored code and generated headers | simdutf 9.0.0 (including its BSD attribution), stb image/resize notices, GLAD 2.0.8 license collection and Khronos header notice |
| Embedded font fallbacks | JetBrains Mono 2.304 variable upright/italic, Nerd Fonts Symbols Only 3.4.0, Noto Color Emoji and Noto Emoji text; source-tree font attribution/OFL and nine pinned glyph-source notice/README files retained |
| `share/ghostty/themes` | Exact 602-file catalog, collection LICENSE, CREDITS and the README credits section from iTerm2-Color-Schemes `0173c3cc154aab5d43b03241286d32372a87dec6`; individual licenses remain unresolved below |
| `bin/zmx` | Its separate Ghostty VT revision `8af6897c0afc63037a8a3efee4162a380e3a4572`, uucode, Highway and simdutf; the existing zmx LICENSE remains retained separately |
| Zig-generated runtime/C++ support | Zig 0.16.0 MIT license and its bundled libc++, libc++abi and libunwind license texts |
| Swift core, concurrency, observation, synchronization, Glibc and builtin float | Swift 6.3.2 LICENSE with Runtime Library Exception |
| Swift RegexParser / StringProcessing | swift-experimental-string-processing at its `swift-6.3.2-RELEASE` commit |
| Foundation Essentials / Internationalization | swift-foundation LICENSE and full NOTICE |
| Foundation compatibility / Dispatch / BlocksRuntime | swift-corelibs-foundation and swift-corelibs-libdispatch licenses with Runtime Library Exceptions |
| `lib_FoundationICU.so` | Swift Foundation ICU integration license, upstream ICU 76.1 notice and Apple ICU-76104.3 notice |
| Swift code linked into executables | ArgumentParser 1.8.2 and TOMLDecoder 0.4.5 at the exact `agterm-linux/Package.resolved` revisions |

`SharedDeps.zig`, `Config.zig`, `GhosttyZig.zig` and the Linux setup options establish the default Linux input set.
The inventory deliberately includes notices for declared inputs even where optimization may remove unused code; it does not assert every symbol is present in a binary.
Darwin-only Objective-C/macOS/libintl, wasm JavaScript, disabled Linux Sentry/Breakpad, Ghostty's unused GTK frontend and test-only fonts/pixels are outside this payload map.
Host GTK, WebKit, glibc and other system libraries are not copied by adding these notices.

The Symbols Only font bytes match the primary Nerd Fonts 3.4.0 source font exactly.
The inventory retains copyright/license name-table records from the actual Symbols Only and Noto font binaries, including the Noto text font's 2021 copyright.
Glyph license texts supplied in the pinned Nerd Fonts source are retained, including Codicons, Font Awesome, Material Design, Octicons, Pomicons, Powerline and Weather Icons.
These notices do not establish logo/trademark permissions or clear any glyph source lacking a separate retained notice.

The simdutf Zig wrapper advertises 5.2.8, but the actual vendored header declares 9.0.0.
The inventory follows the vendored source and retains its additional BSD notice, rather than substituting the wrapper's stale version.
The zmx Ghostty revision's simdutf files and Highway input match those of the GUI Ghostty revision byte-for-byte; its uucode dependency has the same exact URL and Zig hash.
Swift Foundation ICU's import history identifies Apple ICU-76104.3 in commit `54d1ecd320702900b6ac76eae90efacad0249d8e`.
That source's full ICU notice is retained in addition to the integration license and stock ICU notice; these source relationships do not attest arbitrary external toolchain builds.

Portions of this software are copyright © 2023 The FreeType Project (www.freetype.org).
All rights reserved.
The FreeType License alternative is retained in `freetype/docs-FTL.TXT`, together with its selection guidance and the other offered license text.
Its BDF/PCF, hash, gzip and HarfBuzz-derived notices are retained separately.
The unmodified z2d MPL-covered source is available from the exact source archive URL and SHA256 in `inventory.json` (`archives.z2d`); its `COPYING` and full `LICENSE` are retained.
The Linux build does not patch z2d.

## Provenance and validation

Git-source entries identify an immutable commit, path and raw URL.
Archive entries identify an exact member and refer to an archive URL/SHA256 in the inventory.
All 21 Ghostty dependency archives were downloaded and their Zig package hashes independently recomputed with the checksum-verified Zig 0.16 fetch utility; no build or candidate execution was involved.
The retained pinned Ghostty manifests bind each dependency name, URL and Zig hash, and the helper rejects disagreement.
The Zig source archive SHA256 matches the official 0.16.0 download index.
An `excerpt` records zero-based byte offsets, with exclusive end, and the full original source SHA256; the retained excerpt bytes are unedited.
These excerpts preserve notices embedded in compiled headers and the theme README credits without copying unrelated implementation or installation instructions.

`linux-notices.py verify-profile` validates retained file/inventory hashes, source URL/revision bindings, dependency declarations, component associations, excerpt ranges and the complete theme inventory.
The theme archive URL/SHA256 must also match `linux/ghostty-resources.env`.
Missing, changed, empty or linked notice files/directories fail validation before staging creates a notice tree.
The existing Ghostty cache LICENSE/provenance checks remain in force.
The existing CI cache key already includes the entire notice directory and helper, so this update invalidates older hosted caches without a workflow edit.

Staging retains repository MIT and zmx licenses and hashes the actual copied libraries in `payload.json`.
The payload records the reviewed component association for each copied library.
An optional compatibility library such as ncurses has an empty association and is explicitly listed in `unresolved_libraries` until its exact notices and mapping are added; existing local installs remain supported.
A filename mapping is not a source attestation or permission to substitute an arbitrary Swift version.
Review a different toolchain before distributing its payload.

## Remaining distribution decisions

All 602 theme files match their corresponding pinned source files byte-for-byte and have individual hashes in the inventory.
The collection license explicitly reserves each theme's copyright/license to its own author.
The retained credits identify upstream authors and links, but those links are not immutable per-theme license proofs.
Every theme remains marked `unresolved-individual-author-license`; this update does not silently declare the collection cleared under MIT.
A publication decision must reconcile those individual terms or explicitly change the shipped catalog in a separate reviewed source change.

This is bounded notice retention, not exhaustive certification of every source-embedded attribution or of Zig-generated runtime code.
The component map also does not prove the build origin of externally supplied Swift/runtime bytes.
Any added/replaced library or source revision needs renewed reconciliation.
No historical archive is rebuilt or changed by this source work.
Accepted `6775c336` / run `41357189891` evidence remains immutable and applies only to those historical bytes; a new candidate requires new artifact and native acceptance.
