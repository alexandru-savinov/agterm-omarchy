# Contributing to agterm

## Contributing to the Omarchy port

This repository is [alexandru-savinov/agterm-omarchy](https://github.com/alexandru-savinov/agterm-omarchy), the Omarchy build of agterm.
The text from "Before you start" onward is upstream agterm's macOS guide, kept unchanged; for this repository, this section comes first.

- **Report a bug.** Search the [issues](https://github.com/alexandru-savinov/agterm-omarchy/issues) first, then use the
  [bug form](https://github.com/alexandru-savinov/agterm-omarchy/issues/new?template=bug.yml).
  [SUPPORT.md](SUPPORT.md) lists what to include. Send suspected vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue.
- **Ask a question or propose an idea** in [Discussions](https://github.com/alexandru-savinov/agterm-omarchy/discussions).
  The pinned [Status and roadmap](https://github.com/alexandru-savinov/agterm-omarchy/issues/525) issue says what is being worked on.
- **Send a pull request.** Open it as a draft against `main`, link the issue it fixes, and fill in the
  [template](.github/pull_request_template.md), including how you tested it. Comment on the issue first if you plan a large change.

How our automation works: [docs/AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md).

### Before you write code

Keep `agtermCore/` host-free and compatible with upstream.
Put GTK behavior in `agterm-linux/`, and Omarchy integration in Linux scripts or packaging.
[AGENTS.md](AGENTS.md) has the branch and parity boundaries; send macOS changes upstream on a separate branch.
Read the matching `.claude/rules/` notes before changing a subsystem; Linux main-loop dispatch uses its GLib adapter.
Keep GUI and control API behavior aligned, update the bundled agent skill for protocol changes, and leave CHANGELOG.md to the release flow.

### Testing

The [Omarchy build guide](packaging/linux/omarchy.md) starts with the clone command and lists test prerequisites.
Use `--with-ui-tests` during setup for the additional test tools and explicitly choose Debug or the installed Release executables.
Run the tests that fit the change: the installer and script tests for packaging, the core and Linux app tests for Swift code,
and, for anything that changes drawing or input, a test on a real Omarchy desktop.
Use a separate test desktop or user for that, not your everyday one:
setting `AGTERM_STATE_DIR` alone still shares Ghostty's configuration files with other running copies (#124/#126).
Never point tests at the control socket of the agterm you are using.
In the pull request, say which commit and architecture you tested, whether it ran on Wayland or X11, the commands you ran and what happened,
and link any (sanitized) logs; say which tests you skipped or saw fail.
The automated tests run under X11 on GitHub's servers, so they do not prove that something works on a real Omarchy desktop.

The **Omarchy CI** workflow runs the automated tests on pushes to `main`, on pull requests to `main` or `omarchy`, and on demand:
installer checks, SwiftLint, the core tests and app builds on x86_64 and ARM64, and accessibility tests on a virtual X11 display.
Failed app tests upload diagnostics.
The aggregate **CI** check is the required status check.
Releases are cut from `main` as described in the [README](README.md#releases-and-verification).

A maintainer reviews every pull request before it merges.
A review by another AI agent working on the same GitHub account does not count as independent approval.

## Before you start

If this is your first pull request to agterm, open a [Discussion](https://github.com/umputun/agterm/discussions) describing what you plan to build and why, then wait for a reply before writing code. Some ideas do not fit the project's direction, and finding that out early costs less than finding it out after a finished PR.

Check that what you want does not already exist: agterm carries a broad control API, custom commands you bind yourself in `keymap.conf`, and six Settings tabs, so a lot is reachable without new code. Look at `agtermctl --help`, the [README](README.md), [agterm.com/docs](https://agterm.com/docs), and the bundled agent skill under `plugins/agterm/skills/agterm/` before proposing anything.

Discussions are for ideas, questions, and anything open-ended. Issues are for concrete bugs and for features already agreed in a Discussion.

## Is it worth it?

Weigh what a feature adds against the code it brings with it.

- **Does it help most users, or one setup?** A feature covering a handful of edge cases rarely pays for its maintenance cost.
- **Does it belong in agterm or upstream in ghostty?** agterm is a SwiftUI shell around libghostty with a workspace and session sidebar on top. Rendering, terminal emulation, key handling, font behavior, and escape-sequence support all come from libghostty, so bugs and feature requests in those areas belong upstream in [ghostty](https://github.com/ghostty-org/ghostty). Working around a libghostty problem inside agterm is the wrong layer, and a PR doing it will be rejected on that ground alone.
- **Does it change what agterm is?** It is a terminal with a two-level workspace to session sidebar. It is not a multiplexer, not a session manager for other tools, not an IDE. PRs pulling it in those directions will be closed.
- **Is the code proportional to the value?** Several hundred lines for something a `keymap.conf` custom command already covers is a hard sell. Keep it small.

## Development setup

Building needs macOS 14 or later, Xcode 26 with `xcodegen` on `PATH`, and Homebrew. `xcodegen` is not installed for you and nothing checks for it, but `make build`, `make run`, `make release` and `make test-app` all call it, so install it first (`brew install xcodegen`). Run the one-time setup next:

```
scripts/setup.sh
```

It builds `GhosttyKit.xcframework`, the Ghostty resources, and zmx from pinned upstream source using the Homebrew `zig@0.17` keg. The Ghostty build also uses Xcode's Metal Toolchain (downloaded automatically on the first run). It takes a few minutes. Later runs skip the build once the artifacts are present, so day-to-day work pays nothing.

After that:

| command | what it does |
|---|---|
| `make build` | Debug build, no launch |
| `make test` | host-free `agtermCore` unit tests through `swift test` |
| `make test-app` | application-hosted AppKit unit tests |
| `make lint` | `swiftlint lint --strict` over the tree |

All four must be green before you send a PR. `make lint` runs with `--strict`, so a warning fails it and the tree is kept at zero findings.

The OpenCode status-plugin tests inside `make test` spawn Node.js, so they need 22.7+ (or 20.19+ on the 20.x line) on `PATH`; those versions unflag module-syntax detection for the plugin's bare `.js`. Without a qualifying Node they skip, and the app itself never needs Node at runtime.

None of those four runs the XCUITests in `agtermUITests/`. Those need the `agterm` scheme, which drives the running app through the accessibility API:

```
xcodebuild test -project agterm.xcodeproj -scheme agterm -destination 'platform=macOS'
```

Run it when your change touches UI behavior. It takes several minutes and wants the machine left alone while it drives the app, so the maintainer also runs it before merging.

`make prep`, `make generate`, `make run`, `make release`, `make deploy`, `make dist` and `make clean` cover the rest. A bare `make` lists them.

## AI-assisted contributions

AI-assisted development is welcome. The expectations for those PRs are specific.

**Quality expectations**

- Review your own code before submitting it, whether you or a tool wrote it.
- Follow the project's conventions. Read the codebase first, with or without AI help. The ones that catch people out:
  - **Module boundary.** `agtermCore` is host-free: no GhosttyKit, no AppKit, no Metal, and no CoreGraphics geometry types (`CGSize`, `CGRect`, `CGFloat`). That is what lets `swift test` run with no app host. Model, persistence, parsing, and dispatch logic go there; SwiftUI, AppKit, and libghostty code stays in the app target. When a feature splits, push the host-free half down into `agtermCore` and keep the app target a thin adapter for side effects.
  - **Concurrency.** Swift 6 with strict concurrency set to `complete`. Every touch of main-actor state from a libghostty C callback hops through `DispatchQueue.main.async`, since those callbacks are not `@MainActor`, and `assumeIsolated` is not used at that boundary.
  - **Tests.** `agtermCore` uses Swift Testing (`@Test`, struct suites); the app-hosted target uses XCTest. Add focused coverage to the suite that already owns the behavior, and create a new test file only when that gives clearer ownership. Suites here are organized by concern rather than one per source file: `AppStore.swift` is covered by twelve of them, split by area (focus, navigation, panes, organization, restore, events, and more).
  - **Documentation.** A change to the control API, the keymap format, or the window, workspace and session model also updates the bundled agent skill under `plugins/agterm/skills/agterm/` and the website under `site/`. Leave `CHANGELOG.md` alone; it is written at release time only.
- Code has to be readable by a person maintaining it a year from now.
- Commit messages and PR descriptions should say something specific. Generic AI output is not enough.
- Keep PRs focused. Unrelated cleanup does not belong in a feature PR, so send it separately.
- Checking AI output for security problems is your job. These tools introduce vulnerabilities that are not obvious on a skim.
- You must understand and be able to explain every line you submit. If asked about your changes during review, "the AI wrote it" is not an acceptable answer.

**Reviewable scope**

- A PR has to be sized for a human to review.
- Split a large change into focused, logical PRs.
- A PR touching dozens of files with thousands of lines is not reviewable. Break it down.

**What will not be accepted**

- Unreviewed AI output dumped for the maintainer to clean up.
- Code with no tests, or with a failing test or lint run.
- Changes that ignore project conventions after being pointed at them.
- PRs that do not respond to review feedback.

A PR violating these may be closed without further discussion. Contributions are valued, but the project cannot act as free QA for bulk AI-generated code.

## Adding a user-facing action

This is the convention you cannot guess from the code: a new user action is not done when the button works.

Anything added to `AppActions` or `AppStore` also needs all four of:

1. a `Command` case, with its arguments, in `agtermCore`'s `ControlProtocol.swift`
2. handling in `ControlDispatcher`, which owns the host-free part (argument parsing, validation, error text, response shape) and calls the app through a `ControlActions` method for the side effect alone
3. an `agtermctl` subcommand in `agtermctlKit`
4. protocol round-trip tests plus an end-to-end test

The toolbar, the menu bar, and the control socket are three callers of the same seam, and they must not drift apart. A command that sets or mutates per-session state also owes a matching read-back field on the `tree` node, so a script can query the value it just wrote.

Two documents mirror the control API and move with it: the bundled agent skill under `plugins/agterm/skills/agterm/`, and the per-command reference at `site/commands.html`.

The one exemption is chrome with nothing to drive, meaning pure rendering or visual polish. Say so in the PR description when you claim it.

## Issues and PRs

Every issue and pull request has to answer two questions:

1. **What is the problem?** What exactly is broken, missing, or awkward. Be specific. "It would be nice to have X" is not a problem statement.
2. **How does this solve it?** Why this particular approach is the right fix, and how it addresses the cause rather than the symptom.

A PR with no problem statement will be closed. If the problem is hard to articulate, the solution is probably not needed.

## Cookbook recipes

`cookbook/` collects installable `agtermctl` workflows. A recipe is a shell script and a README rather than Swift, so it follows its own rules: a fixed README template, an index row, and a version pin.

Read [cookbook/CONTRIBUTING.md](cookbook/CONTRIBUTING.md) before sending one.
