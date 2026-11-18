# Theme licence resolution, pass 2

Pass 2 starts from `themes.json` and re-examines only four drop buckets. Every other drop is unchanged.

- Organisation-page credits.
- Linked repositories that return 404.
- Adapter and port drops.
- Gist-only credits.

Each newly accepted repository was pinned and licence-checked as in pass 1: default-branch HEAD SHA, `repos/O/R/license?ref=<sha>`, raw download at that SHA with its sha256, and an MIT body and rider check.

## Counts

| | Pass 1 | Pass 2 |
|---|---|---|
| Resolved | 118 | 181 |
| Dropped | 701 | 638 |

63 themes changed from DROP to RESOLVED. No theme went the other way.

By basis: `origin-license` 55, `org-official-repo` 8

## Every change (DROP to RESOLVED)

| Theme | Basis | Repository | SPDX | Revision | Licence file |
|---|---|---|---|---|---|
| Catppuccin Frappe | org-official-repo | catppuccin/iterm | MIT | `b2936a6e5527` | `licenses/catppuccin__iterm/LICENSE` |
| Catppuccin Latte | org-official-repo | catppuccin/iterm | MIT | `b2936a6e5527` | `licenses/catppuccin__iterm/LICENSE` |
| Catppuccin Macchiato | org-official-repo | catppuccin/iterm | MIT | `b2936a6e5527` | `licenses/catppuccin__iterm/LICENSE` |
| Catppuccin Mocha | org-official-repo | catppuccin/iterm | MIT | `b2936a6e5527` | `licenses/catppuccin__iterm/LICENSE` |
| Rose Pine | org-official-repo | rose-pine/iterm | MIT | `4801702ac306` | `licenses/rose-pine__iterm/LICENSE` |
| Rose Pine Dawn | org-official-repo | rose-pine/iterm | MIT | `4801702ac306` | `licenses/rose-pine__iterm/LICENSE` |
| Rose Pine Moon | org-official-repo | rose-pine/iterm | MIT | `4801702ac306` | `licenses/rose-pine__iterm/LICENSE` |
| Srcery | org-official-repo | srcery-colors/srcery-terminal | MIT | `ce4e4709a535` | `licenses/srcery-colors__srcery-terminal/LICENSE` |
| GitLab Dark | origin-license | albertorestifo/gitlab-webide-theme | MIT | `b372d44ae235` | `licenses/albertorestifo__gitlab-webide-theme/LICENSE` |
| GitLab Dark Grey | origin-license | albertorestifo/gitlab-webide-theme | MIT | `b372d44ae235` | `licenses/albertorestifo__gitlab-webide-theme/LICENSE` |
| GitLab Light | origin-license | albertorestifo/gitlab-webide-theme | MIT | `b372d44ae235` | `licenses/albertorestifo__gitlab-webide-theme/LICENSE` |
| Ayu Mirage | origin-license | ayu-theme/vscode-ayu | MIT | `605c9c850468` | `licenses/ayu-theme__vscode-ayu/LICENSE` |
| Challenger Deep | origin-license | challenger-deep-theme/vim | MIT | `e3d5b7d9711c` | `licenses/challenger-deep-theme__vim/LICENSE` |
| Iceberg Dark | origin-license | cocopon/iceberg.vim | MIT | `23835d5ed696` | `licenses/cocopon__iceberg.vim/LICENSE.txt` |
| Iceberg Light | origin-license | cocopon/iceberg.vim | MIT | `23835d5ed696` | `licenses/cocopon__iceberg.vim/LICENSE.txt` |
| Cobalt Next | origin-license | davidleininger/cobaltnext-vscode | MIT | `bfd077cfe679` | `licenses/davidleininger__cobaltnext-vscode/LICENSE` |
| Cobalt Next Dark | origin-license | davidleininger/cobaltnext-vscode | MIT | `bfd077cfe679` | `licenses/davidleininger__cobaltnext-vscode/LICENSE` |
| Cobalt Next Minimal | origin-license | davidleininger/cobaltnext-vscode | MIT | `bfd077cfe679` | `licenses/davidleininger__cobaltnext-vscode/LICENSE` |
| Nord Wave | origin-license | dimitrisnl/nord-wave | MIT | `a5834a6aa1d2` | `licenses/dimitrisnl__nord-wave/LICENSE` |
| Doom One | origin-license | doomemacs/themes | MIT | `a59202912ad5` | `licenses/doomemacs__themes/LICENSE` |
| Doom Peacock | origin-license | doomemacs/themes | MIT | `a59202912ad5` | `licenses/doomemacs__themes/LICENSE` |
| Poimandres | origin-license | drcmda/poimandres-theme | MIT | `737d14190aa7` | `licenses/drcmda__poimandres-theme/LICENSE` |
| Primary | origin-license | google/vim-colorscheme-primary | Apache-2.0 | `97a6523106e4` | `licenses/google__vim-colorscheme-primary/LICENSE` |
| Spacedust | origin-license | hallski/spacedust-theme | MIT | `bb5d0a364da6` | `licenses/hallski__spacedust-theme/LICENSE` |
| Horizon | origin-license | jolaleye/horizon-theme-vscode | MIT | `5ae91b6d49bf` | `licenses/jolaleye__horizon-theme-vscode/LICENSE` |
| Horizon Bright | origin-license | jolaleye/horizon-theme-vscode | MIT | `5ae91b6d49bf` | `licenses/jolaleye__horizon-theme-vscode/LICENSE` |
| Rouge 2 | origin-license | josefaidt/rouge-theme | MIT | `4b2115c4a9da` | `licenses/josefaidt__rouge-theme/LICENSE` |
| Noctis | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Azureus | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Bordo | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Hibernus | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Lilac | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Lux | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Minimus | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Obscuro | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Sereno | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Uva | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Noctis Viola | origin-license | liviuschera/noctis | MIT | `4a82370b2c06` | `licenses/liviuschera__noctis/LICENSE.md` |
| Black Metal | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Bathory) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Burzum) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Dark Funeral) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Gorgoroth) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Immortal) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Khold) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Marduk) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Mayhem) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Nile) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| Black Metal (Venom) | origin-license | metalelf0/base16-black-metal-scheme | MIT | `c7a32bec8105` | `licenses/metalelf0__base16-black-metal-scheme/MIT-LICENSE` |
| GitHub Dark Colorblind | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| GitHub Dark Default | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| GitHub Dark Dimmed | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| GitHub Dark High Contrast | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| GitHub Light Colorblind | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| GitHub Light Default | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| GitHub Light High Contrast | origin-license | primer/github-vscode-theme | MIT | `cd78e5e4e7bc` | `licenses/primer__github-vscode-theme/LICENSE` |
| Mellifluous | origin-license | ramojus/mellifluous.nvim | MIT | `ec0575bdb635` | `licenses/ramojus__mellifluous.nvim/LICENSE` |
| Synthwave Everything | origin-license | robb0wen/synthwave-vscode | MIT | `ecfa2fe1279f` | `licenses/robb0wen__synthwave-vscode/LICENSE` |
| Sonokai | origin-license | sainnhe/sonokai | MIT | `b023c5280b16` | `licenses/sainnhe__sonokai/LICENSE` |
| Night Owlish Light | origin-license | sdras/night-owl-vscode-theme | MIT | `cc291eba7976` | `licenses/sdras__night-owl-vscode-theme/LICENSE.md` |
| Duotone Dark | origin-license | simurai/duotone-dark-syntax | MIT | `d0ce468711a8` | `licenses/simurai__duotone-dark-syntax/LICENSE` |
| Alabaster | origin-license | tonsky/sublime-scheme-alabaster | MIT | `9ee7d87314fa` | `licenses/tonsky__sublime-scheme-alabaster/LICENSE.txt` |
| Hyper | origin-license | vercel/hyper | MIT | `890a332ed9c3` | `licenses/vercel__hyper/LICENSE` |

## Re-examined but kept DROP

**Organisation-page credits.** All 8 resolved:

- Catppuccin uses `catppuccin/iterm`.
- Rosé Pine uses `rose-pine/iterm`.
- Srcery uses `srcery-colors/srcery-terminal`.

All three are MIT, non-fork, not archived, and the official iTerm2/terminal port of each organisation.

**Linked repositories that return 404.** All 21 stay DROP. `gh api repos/O/R` returns 404 for each one, meaning there is no rename redirect (a rename answers with a 301 to the new name). Each owner's repository list was searched for the same project name:

- `bahlo/iterm-colors` and `sayeefm0/iterm2-wilmersdorf`: no matching repository.
- `vinkla/broadcast-theme`: no matching repository.
- `baskerville/iTerm-2-Color-Themes`: the owner only has `xresources-color-schemes`, a different project.
- `guesswhozzz`: the user no longer exists.
- `michael-andreuzza/Serendipity-Theme` and `Sequoia-Theme`: the owner has only `Serendipity-Theme-Zed`, `serendipity-obsidian`, `zed-sequoia-theme` and `sequoia-obsidian`. These are separate per-app ports, so they are not accepted as renames.

Themes affected: Arthur, Atom, Broadcast, Brogrammer, Darkside, Glacier, Guezwhoz, N0Tch2K, Pnevma, Sequoia Monochrome Dark, Sequoia Monochrome Light, Sequoia Moonlight Dark, Sequoia Moonlight Light, Sequoia Retro Dark, Sequoia Retro Light, Serendipity Midnight, Serendipity Morning, Serendipity Sunset, Square, Wilmersdorf, Wryan

**Gist-only credits.** All 7 stay DROP. None of the gists carries a licence text: each holds only a single `.itermcolors` file, and `meqif/1238378` (IR Black) now returns 404.

Themes affected: Borland, Hybrid, IR Black, Solarized Dark Higher Contrast, Teerb, Twilight, Wez

**Adapter and port drops kept as DROP:**

- Breeze: origin-license: KDE/konsole licence NOASSERTION not in the allowed set
- Cyberpunk Scarlet Protocol: origin-license: prometheux-ar/cyberpunk licence GPL-3.0 not in the allowed set
- Earl Grey: ANSI palette was derived for this port (origin publishes only bg/fg/accent); not a mechanical conversion
- Fahrenheit: origin-license: fcpg/vim-fahrenheit has no licence file
- JetCalm Light: credit says "based on One Half Light, inspired by the JetBrains palette"; a derived palette, not a port
- Karma Dark: origin is the Karma VS Code theme linked only as a website (sreetamdas.com); no origin repository to pin
- Karma Dark Dimmed: origin is the Karma VS Code theme linked only as a website (sreetamdas.com); no origin repository to pin
- Karma Dark HC: origin is the Karma VS Code theme linked only as a website (sreetamdas.com); no origin repository to pin
- Karma Light: origin is the Karma VS Code theme linked only as a website (sreetamdas.com); no origin repository to pin
- Karma Light Dimmed: origin is the Karma VS Code theme linked only as a website (sreetamdas.com); no origin repository to pin
- Karma Light HC: origin is the Karma VS Code theme linked only as a website (sreetamdas.com); no origin repository to pin
- Mirage: origin-license: tristanremy/mirage has no licence file
- Molokai: origin LICENSE.md grants MIT for molokai.vim source only and states the Monokai theme is copyright Wimer Hazenberg
- Outrun Electric: origin-license: samrap/outrun-theme-vscode has no licence file
- Overnight Slumber: origin-license: cevr/overnight has no licence file
- Paul Millr: origin-license: paulmillr/dotfiles has no licence file
- Red Planet: origin-license: eliquious/Red-Planet-Theme has no licence file
- Seti: Seti was modified for the collection after porting (ANSI blacks changed); not a mechanical conversion, origin licence alone does not cover it
- Snazzy Soft: credit says "inspired by" Snazzy with a new gentler palette; a derived palette, not a port

## Judgment calls

- **Origin choice.** "Origin" is the repository the credit names as the creator's theme. The resolved fields point at that repository, and `credit` keeps the full credit line, including the porter.
  - Ayu Mirage's origin `ayu-theme/vscode-ayu` is a GitHub fork of `dempfi/ayu`, the original Ayu repository, not of the collection. It is licensed MIT.
  - Spacedust's link `mhallendal/spacedust-theme` redirects to `hallski/spacedust-theme`, and Doom's `hlissner/emacs-doom-themes` redirects to `doomemacs/themes`. Both are recorded under the canonical name.
- **"A port is a mechanical conversion" was not applied where the credit itself describes new colour work.** That covers Seti (blacks modified after porting), Earl Grey (ANSI set derived), Snazzy Soft ("inspired by", new gentler palette) and JetCalm Light (based on One Half Light plus a JetBrains-inspired palette). Earl Grey's origin `earl-grey-theme/earl-grey` is now detected as Apache-2.0 via `LICENSE-APACHE.txt`, and Seti's creator repo `jesseweed/seti-syntax` is MIT. Promote either if derived palettes are acceptable.
- **Molokai stays DROP.** The origin `LICENSE.md` licenses molokai.vim source under MIT but states "monokai theme copyright Wimer Hazenberg", so the palette itself is not granted.
- **Breeze stays DROP.** `KDE/konsole` `COPYING` is the GPL, and GitHub reports NOASSERTION. Its text is in `licenses-dropped/`, along with `prometheux-ar/cyberpunk` (GPL-3.0).
- **Mirage, Overnight Slumber and Karma stay DROP.** The origins `tristanremy/mirage` and `cevr/overnight` have no licence file, and Karma's origin is a website only.

## Well-known families

**Changed to RESOLVED**
- Catppuccin (4)
- Rosé Pine (3)
- Srcery
- GitHub: the 7 Primer-derived themes (Dark/Light Default, Dimmed, Colorblind, High Contrast)
- GitLab (3)
- Noctis (11)
- Black Metal (11)
- Iceberg (2)
- Doom One and Doom Peacock
- Horizon (2)
- Cobalt Next (3)
- Nord Wave
- Poimandres (base theme only)
- Sonokai, Night Owlish Light, Hyper and Primary

**Unchanged.** None of these drops was in the pass-2 buckets:
- **Dracula:** `zenorocha/dracula-theme` has no licence file, and the Dracula+ credit is profile-only.
- **Gruvbox:** every credit links only the `morhetz` profile.
- **Nord:** the credit links the nordtheme.com website and a profile; Nord Light was already resolved in pass 1.
- **Solarized:**
  - The iTerm2 Solarized schemes are built-ins.
  - Solarized Dark Higher Contrast is a gist with no licence.
  - Solarized Darcula is profile-only.
  - Solarized Dark Patched has no credit.
  - Solarized Osaka Night was already resolved in pass 1.
- **GitHub and GitHub Dark:** both are profile-only credits.

If the organisation-repository logic should also cover these profile-only family credits, it is the same mechanism: `dracula/iterm`, `morhetz/gruvbox` or `gruvbox-community`, `nordtheme/iterm2`. That would be a further, explicitly scoped pass.
