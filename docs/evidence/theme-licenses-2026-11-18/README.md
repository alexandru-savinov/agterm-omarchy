# Theme license research, 2026-11-18 (#463)

`decisions.json` holds the final decision for each of the 819 catalog themes: resolved (repository, revision, license path, SPDX id, sha256, basis) or dropped (reason).
`pass1.md`, `pass2.md` and `pass3.md` record each pass's rule and every change it made.
`gen_notices.py` folds the decisions into `packaging/linux/notices`. It needs the license texts, which are retained there, and the theebest NOTICE.
Run it as `python3 gen_notices.py REPO RESEARCH_DIR decisions.json`, where RESEARCH_DIR holds `licenses/<owner>__<repo>/` and `extra/`.

By basis, the 231 resolved themes are:

| Basis | Themes |
| --- | --- |
| The credited repository itself | 118 |
| `org-official-repo` | 8 |
| `origin-license` | 55 |
| `author-own-repo` | 50 |

For the 113 inferred themes, the license belongs to a repository the credit implies, not one it links. Nobody compared those themes' palettes with the pinned repository. Accepting that inference is the owner's decision.
