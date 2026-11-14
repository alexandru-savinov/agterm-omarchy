import Foundation
import agtermCore

extension ConfigPaths {
    static func linuxStarterKeymapConf() -> String {
        let actionLines = BuiltinAction.allCases.map { action -> String in
            let syntax: String
            if let chord = action.linuxDefaultChord {
                let rendered = chord.displayString
                syntax = parseKeybind(rendered) == [chord] ? rendered : "(not expressible)"
            } else {
                syntax = "(no default)"
            }
            return "#   \(action.rawValue)  \(syntax)"
        }.joined(separator: "\n")
        let tokenLines = CommandContext.tokenNames.map { "#   {\($0)}" }.joined(separator: "\n")
        return """
        # agterm Linux keymap. Edit and run `agtermctl keymap reload` to apply.
        # Blank lines and comments are ignored; `agtermctl keymap list` reports diagnostics.
        #
        # map <chord> [--repeat] <action>
        # command "<name>" [<chord>] [--repeat] <shell...>
        # Chords join modifiers with +: ctrl, shift, opt (Alt), cmd (Super).
        # Use shift+= for + and shift+/ for ?. Alternatives use |; leader sequences use >.
        # Ctrl+Tab and Ctrl+1/2 are reserved. Avoid chords already assigned below.
        # Commands without a chord appear in the custom-command palette.
        # Commands run detached via /bin/sh -c, without a terminal; use an overlay for a TUI.
        # They inherit the app's environment, not interactive shell startup files.
        # Use an absolute executable path when it is absent from the app's PATH.
        # Configure system-wide shortcuts in your Linux desktop or compositor.
        #
        # Examples (uncomment and edit):
        # map ctrl+shift+l toggle_split
        # command "Deploy" ctrl+opt+e ./deploy.sh
        # command "Build" ./build.sh
        #
        # Built-in actions (raw name followed by Linux shipped default chord):
        \(actionLines)
        #
        # Custom-command tokens (also exported as $AGT_X environment variables):
        \(tokenLines)
        # Token substitution with {AGT_X} is RAW shell text. Remote titles, paths and selections
        # can contain shell syntax. Prefer quoted variables such as "$AGT_SELECTION" instead.

        """
    }

    static func linuxStarterHooksConf() -> String {
        let kinds = ControlEventKind.allCases.map(\.rawValue).joined(separator: ", ")
        return """
        # agterm Linux hooks. Edit and run `agtermctl hooks reload` to apply.
        # Blank lines and comments are ignored.
        # Syntax: on <kind> <shell...>
        # Kinds: \(kinds).
        # Shell lines run via /bin/sh -c, detached without a terminal, in the app's working directory.
        # Each line runs one process at a time; busy lines queue 256 events, dropping the oldest.
        # Identical kind+command lines are skipped with a diagnostic.
        # Stdin contains one event JSON object, as printed by `agtermctl events --json`.
        # Variables: AGT_EVENT_KIND, AGT_EVENT_STATUS, AGT_EVENT_HOST, AGT_SESSION_ID,
        # AGT_WORKSPACE_ID, AGT_WINDOW_ID, AGT_SOCKET. Quote variables in shell commands.
        # Pass --socket "$AGT_SOCKET" to agtermctl. Emitting the same event kind can loop;
        # the queue bounds concurrency, not recursion.
        #
        # Examples (create your script before uncommenting):
        # on status "$HOME/.config/agterm/hooks/status.sh"
        # on session.closed "$HOME/.config/agterm/hooks/session-closed.sh"

        """
    }

    /// Prefer the launch environment's home on Linux. `FileManager` resolves the passwd entry here,
    /// ignoring an intentionally isolated HOME (tests, containers, portable launches) and can start a
    /// shell inside the real user's config tree instead.
    static func defaultNewSessionCwd(
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) -> String {
        if let home = environment["HOME"], !home.isEmpty { return home }
        return FileManager.default.homeDirectoryForCurrentUser.path
    }

    static func starterGhosttyConfig() -> String {
        """
        # agterm-scoped ghostty config. This file is loaded after the bundled defaults.
        # Put agterm-only terminal settings here.

        """
    }

    static func starterRestoreDenylist() -> String {
        """
        # Commands that should not be automatically re-run by restore-running-command.
        tmux
        screen
        zellij

        """
    }
}
