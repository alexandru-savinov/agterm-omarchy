# Theme licence resolution, pass 3

Pass 3 starts from `themes-pass2.json` and re-examines only the pass-1 bucket "credit links only a user/org profile or website" (206 themes). All other entries are unchanged, and every pass-2 resolved entry is byte-identical.

**Rule.** A theme is accepted only on a repository owned by the credited author's own account, or by the official organisation that the credited website links.
- A credit line contributor who "added", "contributed" or "ported" the theme is never used.
- For "X based on Y" lines the origin creator Y is used, consistent with pass 2.
- The palette is matched by repository name.
- The author's terminal or iTerm2 port is preferred, otherwise the palette's main repository.
- Pinning and licence checks are the same as pass 1: HEAD SHA, `license?ref=<sha>`, raw download at that SHA with sha256, and an MIT body and rider check.
- New entries carry `"basis": "author-own-repo"`.

## Counts

| | Pass 2 | Pass 3 |
|---|---|---|
| Resolved | 181 | 231 |
| Dropped | 638 | 588 |

50 themes changed from DROP to RESOLVED. No theme went the other way.

## Every change

| Theme | Repository | SPDX | Revision | Licence file |
|---|---|---|---|---|
| Aura | daltonmenezes/aura-theme | MIT | `20924aa0a439` | `licenses/daltonmenezes__aura-theme/LICENSE` |
| Ryuuko | dylanaraps/ryuuko | MIT | `34bd83cc90d0` | `licenses/dylanaraps__ryuuko/LICENSE.md` |
| Andromeda | EliverLara/Andromeda | MIT | `d1abb48c6949` | `licenses/EliverLara__Andromeda/LICENSE.md` |
| Popping And Locking | hedinne/popping-and-locking-vscode | MIT | `18c7632206c0` | `licenses/hedinne__popping-and-locking-vscode/LICENSE` |
| Selenized Dark | jan-warchol/selenized | MIT | `9a753d5c575c` | `licenses/jan-warchol__selenized/LICENSE.txt` |
| Selenized Light | jan-warchol/selenized | MIT | `9a753d5c575c` | `licenses/jan-warchol__selenized/LICENSE.txt` |
| CGA | jdhmtl/cga-theme | MIT | `8ba6ff45e96f` | `licenses/jdhmtl__cga-theme/LICENSE` |
| Flexoki Dark | kepano/flexoki | MIT | `8d723bac4a9a` | `licenses/kepano__flexoki/LICENSE` |
| Flexoki Light | kepano/flexoki | MIT | `8d723bac4a9a` | `licenses/kepano__flexoki/LICENSE` |
| Tearout | leobeosab/tearout.nvim | MIT | `f6211dece572` | `licenses/leobeosab__tearout.nvim/LICENSE` |
| Patina Dark | lmarkmann/patina-theme | MIT | `19c06c8e4211` | `licenses/lmarkmann__patina-theme/LICENSE` |
| Patina Dark Soft | lmarkmann/patina-theme | MIT | `19c06c8e4211` | `licenses/lmarkmann__patina-theme/LICENSE` |
| Patina Lichen | lmarkmann/patina-theme | MIT | `19c06c8e4211` | `licenses/lmarkmann__patina-theme/LICENSE` |
| Patina Light | lmarkmann/patina-theme | MIT | `19c06c8e4211` | `licenses/lmarkmann__patina-theme/LICENSE` |
| Patina Moss | lmarkmann/patina-theme | MIT | `19c06c8e4211` | `licenses/lmarkmann__patina-theme/LICENSE` |
| Patina Stellar | lmarkmann/patina-theme | MIT | `19c06c8e4211` | `licenses/lmarkmann__patina-theme/LICENSE` |
| Material Design Colors | MartinSeeler/iterm2-material-design | MIT | `2b5256774842` | `licenses/MartinSeeler__iterm2-material-design/LICENSE` |
| Nord | nordtheme/iterm2 | MIT | `233a2462e04e` | `licenses/nordtheme__iterm2/license` |
| Oxocarbon | nyoom-engineering/oxocarbon-iterm2 | MIT | `bb1463e94f5d` | `licenses/nyoom-engineering__oxocarbon-iterm2/LICENSE` |
| Midnight In Mojave | OberstKrueger/midnight-in-mojave-iterm | MIT | `81482555ecf1` | `licenses/OberstKrueger__midnight-in-mojave-iterm/LICENSE` |
| Pyrokai | patrickserrano/pyrokai | MIT | `739f196af1d0` | `licenses/patrickserrano__pyrokai/LICENSE` |
| Pyrokai Light | patrickserrano/pyrokai | MIT | `739f196af1d0` | `licenses/patrickserrano__pyrokai/LICENSE` |
| Calamity | Pustur/calamity-vscode | MIT | `137e271c2788` | `licenses/Pustur__calamity-vscode/LICENSE` |
| Rapture | Pustur/rapture-vscode | MIT | `ed423cc62387` | `licenses/Pustur__rapture-vscode/LICENSE` |
| Oceanic Material | rahulpatel/oceanic-material-iterm | MIT | `982e358d953f` | `licenses/rahulpatel__oceanic-material-iterm/LICENSE` |
| Vesper | raunofreiberg/vesper | MIT | `9043f3849b77` | `licenses/raunofreiberg__vesper/LICENSE.md` |
| Galizur | razielanarki/vscode-galizur-theme | MIT | `dfd817a55203` | `licenses/razielanarki__vscode-galizur-theme/LICENSE.md` |
| Kanagawa Dragon | rebelot/kanagawa.nvim | MIT | `bb85e4bfc8d8` | `licenses/rebelot__kanagawa.nvim/LICENSE` |
| Kanagawa Lotus | rebelot/kanagawa.nvim | MIT | `bb85e4bfc8d8` | `licenses/rebelot__kanagawa.nvim/LICENSE` |
| Kanagawa Wave | rebelot/kanagawa.nvim | MIT | `bb85e4bfc8d8` | `licenses/rebelot__kanagawa.nvim/LICENSE` |
| Whimsy | rod/iterm2-whimsy | MIT | `c73fcbeb0e36` | `licenses/rod__iterm2-whimsy/LICENSE` |
| Gruvbox Material | sainnhe/gruvbox-material-tilix | MIT | `5b3505752a7c` | `licenses/sainnhe__gruvbox-material-tilix/LICENSE` |
| Melange Dark | savq/melange-nvim | MIT | `ce42f6b629be` | `licenses/savq__melange-nvim/LICENSE` |
| Melange Light | savq/melange-nvim | MIT | `ce42f6b629be` | `licenses/savq__melange-nvim/LICENSE` |
| Karasu Night | scozu/karasu | MIT | `681fe54e4cc0` | `licenses/scozu__karasu/LICENSE` |
| Karasu Snow | scozu/karasu | MIT | `681fe54e4cc0` | `licenses/scozu__karasu/LICENSE` |
| Firewatch | SebastianSzturo/firewatch-syntax | MIT | `e5e6665eb206` | `licenses/SebastianSzturo__firewatch-syntax/LICENSE.md` |
| One Half Dark | sonph/onehalf | MIT | `75eb2e97acd7` | `licenses/sonph__onehalf/LICENSE.txt` |
| One Half Light | sonph/onehalf | MIT | `75eb2e97acd7` | `licenses/sonph__onehalf/LICENSE.txt` |
| Sublette | sublee/sublette | MIT | `f3e84e83b29c` | `licenses/sublee__sublette/LICENSE` |
| Kolorit | thomaspaulmann/Kolorit | MIT | `d71fb74a58eb` | `licenses/thomaspaulmann__Kolorit/LICENSE` |
| Blue Dolphin | vimiomori/BlueDolphin | MIT | `285b4235a81a` | `licenses/vimiomori__BlueDolphin/LICENSE` |
| Kanso Ink | webhooked/kanso.nvim | MIT | `1afbbb449aa0` | `licenses/webhooked__kanso.nvim/LICENSE` |
| Kanso Mist | webhooked/kanso.nvim | MIT | `1afbbb449aa0` | `licenses/webhooked__kanso.nvim/LICENSE` |
| Kanso Pearl | webhooked/kanso.nvim | MIT | `1afbbb449aa0` | `licenses/webhooked__kanso.nvim/LICENSE` |
| Kanso Zen | webhooked/kanso.nvim | MIT | `1afbbb449aa0` | `licenses/webhooked__kanso.nvim/LICENSE` |
| Miasma | xero/miasma.nvim | CC0-1.0 | `627f2e1cac91` | `licenses/xero__miasma.nvim/LICENSE` |
| Lit Cell | zerebos/lit-cell | MIT | `ea9b9b32dd23` | `licenses/zerebos__lit-cell/LICENSE` |
| Lit Cell Light | zerebos/lit-cell | MIT | `ea9b9b32dd23` | `licenses/zerebos__lit-cell/LICENSE` |
| Citruszest | zootedb0t/citruszest.nvim | MIT | `c8c0017d1b2e` | `licenses/zootedb0t__citruszest.nvim/LICENSE` |

## Kept DROP

In this bucket, 156 themes remain dropped. Each reason in `themes-pass3.json` now ends with a `pass 3: kept DROP: ...` note.

- credit names no GitHub account and no website that names a GitHub org: Abernathy, Breadog, Cursor Dark, Cursor Light, Dark 2026, Django, Django Smooth, Dot Gov, ENCOM, Fideloper, Flatland, Floraverse, Framer, GitHub, Gruber Darker, Havn Daggry, Havn Skumring, IBM 5153 CGA, IBM 5153 CGA (Black), Idle Toes, Light 2026, Mariana, Night Lion V1, Night Lion V2, Obsidian, SLOMP, Solarized Darcula, Symfonic, Ubuntu, Warm Neon, Zenburn
- monokai-pro/monokai-pro (named by monokai.pro) has no licence file: Monokai Classic, Monokai Pro, Monokai Pro Light, Monokai Pro Light Sun, Monokai Pro Machine, Monokai Pro Octagon, Monokai Pro Ristretto, Monokai Pro Spectrum
- no repository named for this palette under the credited account(s) ReagentX: Banana Blueberry, Blue Matrix, Cyberdyne, Laser, Scarlet Protocol
- idleberg's Paraiso-iTerm2, C64-iTerm2 and other ports have no licence file: Belafonte Day, Belafonte Night, C64, Paraiso Dark
- morhetz/gruvbox has no licence file: Gruvbox Dark, Gruvbox Dark Hard, Gruvbox Light, Gruvbox Light Hard
- no repository named for this palette under the credited account(s) scottmatthews7: London Bonfire Night, London Columbia Road, London Embankment Dusk, London Soho Night
- no repository named for this palette under the credited account(s) pratikmullick: HaX0R Blue, HaX0R Gr33N, HaX0R R3D
- no repository named for this palette under the credited account(s) pyrophos: Batman, Spiderman, The Hulk
- no repository named for this palette under the credited account(s) zasdaym: Atom One Dark, Light Owl, Night Owl
- adapted from the GNOME Adwaita palette, whose origin is not linked: Adwaita, Adwaita Dark
- author 0x3024's repos have no licensed palette repository (3024-builder is a NOASSERTION builder, archived): 3024 Day, 3024 Night
- author's own iTerm port tinacious/iterm-tinacious-design-theme has no licence file; not substituting a sibling repo: Tinacious Design Dark, Tinacious Design Light
- based on iTerm2 Tango Light (iTerm2 is GPL): Tango Adapted, Tango Half Adapted
- credit says only "added by" jos3s; no creator is named: Dracula+, Material Darker
- credit says only "added by" slavamak; no creator is named: Nvim Dark, Nvim Light
- erietz/isocon.nvim has no licence file: Isocon Dark, Isocon Light
- mikemcbride's hyperterm-electron-highlighter is a fork with no licence file: Electron Highlighter, Electron Highlighter Day
- no Raycast palette repository by the credited authors: Raycast Dark, Raycast Light
- no repository by aslilac or lpil identifiable as the Gleam colour palette: Gleam, Gleam Classic
- no repository named for this palette under the credited account(s) Skeeve: IRIX Console, IRIX Terminal
- no repository named for this palette under the credited account(s) Unkulunkulu: Hipster Green, Jackie Brown
- no repository named for this palette under the credited account(s) alebcay: Ayu, Ayu Light
- no repository named for this palette under the credited account(s) bhajneet: Tailwind Dark, Tailwind Light
- no repository named for this palette under the credited account(s) joshka: Aardvark Blue, Aardvark Ink
- port of Google Project IDX theme, whose origin is not linked: Monospace Dark, Monospace Light
- alDuncanson/github-dark is GPL-3.0 (copyleft): GitHub Dark
- author's own iTerm2 port atelierbram/iTerm2_Atelier-Schemes has no licence file (the collection file most plausibly derives from it); not substituting a sibling repo: Atelier Sulphurpool
- author's own terminal port tahayvr/omarchy-sunset-drive-theme has no licence file; not substituting a sibling repo: Sunset Drive
- catthode org has only catthode.github.io, which has no licence file: Catthode
- credited account CodeMonkeyBrian no longer exists: Apple Classic
- credited account Mangeshrex no longer exists: Everblush
- credited account jezzy-ultra no longer exists: Cutie Pro
- credited account msorre2 no longer exists: Purple Rain
- credited account sameerzahir97-cloud no longer exists: Squintless
- credited account spectralkh no longer exists: Chester
- derived light variant of the collection's Apple System Colors theme: Apple System Colors Light
- dogxii's only theme repo (Dogxi-Theme-Zed) has no Misty variant: Dogxi Misty
- elementary/terminal is LGPL-3.0 (copyleft): Elementary
- iamstarkov's one-light-syntax is a fork and hyperterm-one-light has no licence file: Atom One Light
- no repository named for this palette under the credited account(s) 4c696e6b: Violite
- no repository named for this palette under the credited account(s) D3vil0p3r: Box
- no repository named for this palette under the credited account(s) JeffreyCA: Ardoise
- no repository named for this palette under the credited account(s) Kasper-Liu: Ultra Dark
- no repository named for this palette under the credited account(s) Or3stis: Firefox Dev
- no repository named for this palette under the credited account(s) Shriram-Balaji: Nocturnal Winter
- no repository named for this palette under the credited account(s) ZioFester90: Lemon Graphite
- no repository named for this palette under the credited account(s) adoyle-h: Deep
- no repository named for this palette under the credited account(s) akx: Detuned
- no repository named for this palette under the credited account(s) ashwinv11: Tomorrow Night Burns
- no repository named for this palette under the credited account(s) bbeny123: JetBrains Islands Dark
- no repository named for this palette under the credited account(s) brettstil: Synthwave
- no repository named for this palette under the credited account(s) cbitterfield: Coffee Theme
- no repository named for this palette under the credited account(s) dechamp: Purplepeter
- no repository named for this palette under the credited account(s) deneshshan: Slate
- no repository named for this palette under the credited account(s) elenapan [origin dotfile author]: Lovelace
- no repository named for this palette under the credited account(s) filipjanevski: 0x96f
- no repository named for this palette under the credited account(s) fitz123 [co-creator]: Blue Berry Pie
- no repository named for this palette under the credited account(s) h4x3rotab: Phala Green Dark
- no repository named for this palette under the credited account(s) i3d: Matrix
- no repository named for this palette under the credited account(s) indyandie: Arcoiris
- no repository named for this palette under the credited account(s) jakebathman: Unikitty
- no repository named for this palette under the credited account(s) jakmaz: Oxide
- no repository named for this palette under the credited account(s) jeremybahadirli: Apple System Colors
- no repository named for this palette under the credited account(s) jutretel: Jubi
- no repository named for this palette under the credited account(s) kovv: Piatto Light
- no repository named for this palette under the credited account(s) kvrohit: Mellow
- no repository named for this palette under the credited account(s) lukgri: Elegant
- no repository named for this palette under the credited account(s) nicksay: Spacegray Bright
- no repository named for this palette under the credited account(s) peterpme: Under The Sea
- no repository named for this palette under the credited account(s) plyght [website peril.lol links github.com/plyght]: Embers Dark
- no repository named for this palette under the credited account(s) roblatour: Retro
- no repository named for this palette under the credited account(s) stlee42: Firefly Traditional
- no repository named for this palette under the credited account(s) tacolegs2004: Heeler
- no repository named for this palette under the credited account(s) theatlasroom: Lab Fox
- no repository named for this palette under the credited account(s) tw15egan: Bright Lights
- no repository named for this palette under the credited account(s) vitstr: JetBrains Darcula
- no repository named for this palette under the credited account(s) xxxserxxx [adapter of a PD palette]: 12-bit Rainbow
- no repository named for this palette under the credited account(s) yg66: Grey Green
- no repository of equinusocio's named for Palenight: Pale Night Hc
- port of the VS Code default theme, whose origin is not linked: Dark Modern
- sailorhg/fairyfloss has no licence file: Fairyfloss
- theJian/nvim-moonwalk has no licence file: Moonwalk
- vague2k has no repository for the Vague palette under the credited account: Vague
- veteranbv/RetroLegends has no licence file: Retro Legends

## Judgment calls

**Choosing the repository**
- **Credited websites.** Credited websites were fetched and read statically, and only GitHub links they publish were used:
  - nordtheme.com links `arcticicestudio`, whose Nord repositories now live in the `nordtheme` org.
  - stephango.com/flexoki links `kepano/flexoki`.
  - patrickserrano.github.io/pyrokai links `patrickserrano/pyrokai`.
  - martinseeler.com links `MartinSeeler/iterm2-material-design`.
  - tinaciousdesign.com links `tinacious`.
  - monokai.pro/iterm links `monokai-pro/monokai-pro`, which is unlicensed.
- **No credited author account.** Websites without a GitHub account or org link stay DROP. Neither do credits whose only "author" is a product (VS Code default, Cursor, Sublime Text, PyCharm, Ubuntu, Symfony, Apple HIG apart from Midnight In Mojave's own creator) or an "inspired by" mention.
- **Unlicensed iTerm2/terminal port by the same author stays DROP.** When that port exists, the collection file most plausibly derives from it. A licensed sibling repository by the same author was not substituted in that case: Atelier Sulphurpool, Tinacious Design and Sunset Drive.
- **"Based on" lines use the origin creator, as in pass 2:**
  - Andromeda resolves on EliverLara's repository, not memije's.
  - Firewatch resolves on SebastianSzturo's repository.
  - Fairyfloss looks to sailorhg, whose repository is unlicensed.
  - Lovelace looks to elenapan, who has no Lovelace repository.
  - Gleam looks to aslilac and lpil, who have no palette repository.
  - Adwaita, Dark Modern, Monospace, Apple System Colors Light and Tango Adapted are ports or derivatives whose origin is not a linked repository, or is GPL (iTerm2), so they stay DROP.
- **Repository choice among several by the same author:**
  - Gruvbox Material: `sainnhe/gruvbox-material-tilix` (terminal port) is preferred over the main `gruvbox-material`.
  - Calamity and Rapture: `Pustur/calamity-vscode` and `Pustur/rapture-vscode`.
  - Kanso: `webhooked/kanso.nvim`.
  - Oxocarbon: `nyoom-engineering/oxocarbon-iterm2`.
  - Atelier: `atelierbram/base16-atelier-schemes` (MIT) was considered but not used; see the unlicensed-port rule above.
- **Archived but pinned.** `OberstKrueger/midnight-in-mojave-iterm` and `dylanaraps/ryuuko` are archived.

**Licence notes**
- **Galizur.** `razielanarki/vscode-galizur-theme` is NOASSERTION on GitHub. I read the whole 21-line text: it is the MIT text with markdown emphasis and typographic quotes, and it has no additions. Accepted as MIT.
- **Gruvbox.** `morhetz/gruvbox` has no licence file, so Gruvbox Dark, Dark Hard, Light and Light Hard stay DROP. Gruvbox Material (sainnhe) resolves.
- **Dracula+ and Material Darker.** The credit says only "added by jos3s", so no creator is named and both stay DROP. Dracula itself was never in this bucket: `zenorocha/dracula-theme` has no licence file. It is in the no-licence-file bucket and outside this pass.

## Well-known families

**Changed to RESOLVED**
- Nord (`nordtheme/iterm2`)
- Kanagawa (3)
- One Half (2)
- Flexoki (2)
- Selenized Dark and Light
- Gruvbox Material
- Oxocarbon
- Melange (2)
- Kanso (4)
- Patina (6)
- Vesper
- Andromeda
- Aura
- Miasma (CC0-1.0)
- Popping and Locking
- Pyrokai (2)

**Unchanged**
- **Gruvbox (morhetz):** `morhetz/gruvbox` has no licence file.
- **Dracula / Dracula+:** see above.
- **Monokai Pro:** unlicensed.
- **Solarized Darcula:** the credit only says "inspired by".
- **Tinacious:** its iTerm port is unlicensed.
- **GitHub Dark:** `alDuncanson/github-dark` is GPL-3.0.
- **GitHub:** the credit only says "inspired by".
- **elementary:** LGPL-3.0.

**Files.** `cache.pass2.json` is the API cache snapshot before pass 3. `../web/` holds the fetched credit websites; it is evidence only.
