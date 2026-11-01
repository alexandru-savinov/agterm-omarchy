# Bundled component notices

These are unmodified upstream license and notice files, retained with immutable source URLs and SHA256 hashes in `sources.json`.
The profile targets the pinned Ghostty revision and the Swift 6.3.2 runtime used by the Linux CI image.
It is a component-notice collection, not a complete transitive license audit or proof of a library's build origin.

| Component files | Runtime/resource association |
| --- | --- |
| `ghostty/LICENSE` | Pinned libghostty, shell integration and generated terminfo |
| `ghostty-nerd-fonts/LICENSE` | Nerd Fonts source embedded in the pinned Ghostty tree |
| `swift/LICENSE.txt` | Swift core, concurrency, observation, synchronization and platform runtime |
| `swift-string-processing/LICENSE.txt` | Swift RegexParser and StringProcessing runtime |
| `swift-foundation/LICENSE.md`, `NOTICE.txt` | Foundation Essentials and Internationalization, including upstream third-party notices |
| `swift-corelibs-foundation/LICENSE` | Corelibs Foundation compatibility library |
| `swift-corelibs-libdispatch/LICENSE` | Dispatch, Swift Dispatch and BlocksRuntime from the libdispatch distribution |
| `swift-foundation-icu/LICENSE.md` | Swift Foundation ICU integration project |
| `icu-76.1/LICENSE` | ICU 76.1 and its upstream third-party notices |

Swift component commits were resolved from each repository's `swift-6.3.2-RELEASE` tag.
The Swift Foundation ICU revision declares `U_ICU_VERSION "76.1"` in `icuSources/include/_foundation_unicode/uvernum.h`.
The ICU notice comes from the immutable upstream `release-76-1` commit, rather than treating the integration project's Apache license as the ICU license.
The Foundation NOTICE is retained in full.
The Swift and corelibs license texts retain their Runtime Library Exceptions verbatim; this packaging does not reinterpret those exceptions.

`setup-linux.sh` checks the LICENSE from the pinned Ghostty checkout against this profile before installing its cache notice and provenance.
The cache verifier rejects missing, empty, linked, changed or stale notices before accepting that cache.
An old cache fails verification and setup rebuilds it through the existing pinned-source path.
Staging repeats the notice check and retains this collection at `share/licenses/agterm/`, with the repository MIT `LICENSE`, the existing zmx license, and a `payload.json` inventory of the copied shared-library hashes.
The historical `share/agterm/ZMX-LICENSE` path remains available.
The staged `sources.json` identifies notice sources; the package's `share/agterm/COMMIT` identifies agterm source.
Neither a notice-source revision nor a library hash establishes that an arbitrary external toolchain was built from that revision.
For a different Swift/runtime version, review and update the profile before distributing its payload.

## Remaining distribution review

This change closes the concrete omission of the repository MIT license and these pinned component notices from staging.
It does not certify a release as fully cleared for distribution.
Before publication, reconcile the actual build inputs and retained notices for:

- Ghostty's other statically linked Zig dependencies and embedded font sources; the main MIT license does not replace their notices.
- Individual theme attribution in the separately pinned 602-theme catalog.
- Apple-specific ICU modifications and any additional notices beyond the retained Swift integration and upstream ICU 76.1 files.
- Optional compatibility libraries, such as ncurses, copied by the existing dependency-selection rule; their notices are not covered by this profile.
- Any added or replaced library/toolchain version; `payload.json` records the actual copied filenames and bytes for that comparison.

Host-provided GTK, WebKit, glibc and other system libraries are not copied merely by adding this notice collection.
No historical archive is changed by this source fix.
A rebuilt archive needs a new provenance and runtime acceptance decision; the accepted fd85fc58 artifact remains historical evidence for its own bytes.
