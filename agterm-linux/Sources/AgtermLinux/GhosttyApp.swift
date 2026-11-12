// Owns the single `ghostty_app_t` and the libghostty runtime-config callbacks.
// Mirrors the macOS GhosttyApp/GhosttyCallbacks split, adapted to GLib/GTK:
// wakeups coalesce onto a g_idle main-thread tick; the `.render` action draws on
// the app thread (the Linux embedded apprt sets must_draw_from_app_thread).
//
// `@unchecked Sendable` like the macOS GhosttyCallbacks: the C closures fire off
// whatever thread libghostty calls from. The only mutable state (`tickScheduled`)
// is lock-guarded; app/config creation and shutdown run on the main thread.
// Surface-touching action handling runs during a tick on the main thread, asserted
// via `MainActor.assumeIsolated`.
import CGtk
import Foundation
import agtermCore

/// The ghostty-resources candidate dirs (highest priority first): a dev env override, the per-user
/// install, then the system package. Shared by the GHOSTTY_RESOURCES_DIR resolution and the themes
/// lookup so they can't diverge (one resolver for both).
nonisolated func ghosttyResourceCandidates() -> [String] {
    let env = ProcessInfo.processInfo.environment
    let home = FileManager.default.homeDirectoryForCurrentUser.path
    var c: [String] = []
    if let dev = env["AGTERM_GHOSTTY_RESOURCES"], !dev.isEmpty { c.append(dev) }
    c.append("\(home)/.local/share/agterm/ghostty")
    c.append("/usr/share/ghostty")
    return c
}

final class GhosttyApp: @unchecked Sendable {
    static let shared = GhosttyApp()
    private(set) var app: ghostty_app_t?

    private let tickLock = NSLock()
    private var tickScheduled = false
    private var resolvedResources: String?
    private let generatedFiles = Result { try GhosttyGeneratedConfigStorage() }
    private var isShutDown = false

    var currentThemeBackgroundHex: String?
    var currentThemeForegroundHex: String?
    var currentThemePalette: [String] = []
    /// The finalized base config's `font-size`, refreshed with every base config build.
    var currentBaseFontSize: Double?
    @MainActor private var appliedAppearanceSide: LinuxAppearanceSide?

    /// Process environment and locale initialization must precede GTK's worker threads.
    /// Called once from main(), after the GDK policy assignments and before GTK initializes.
    func initializeRuntime() {
        setGhosttyResourcesEnv()   // export GHOSTTY_RESOURCES_DIR before init + buildConfig read it
        ghostty_init(UInt(CommandLine.argc), CommandLine.unsafeArgv)
    }

    @MainActor func start(appearanceSide: LinuxAppearanceSide) {
        guard !isShutDown else { return }
        // Persisted settings (font/size/theme/scroll) are layered on at launch so they survive
        // relaunch. Translucency is omitted: Linux has no window-level compositing yet.
        let saved = linuxSettingsStore().load()
        let lines = AppController.ghosttyLines(for: saved, isDark: appearanceSide.isDark)
        guard let cfg = buildConfig(extraLines: lines) else { return }
        currentThemeBackgroundHex = GhosttyConfigTheme.colors(from: cfg).background
        currentBaseFontSize = GhosttyConfigTheme.fontSize(from: cfg)

        var rt = ghostty_runtime_config_s()
        rt.userdata = Unmanaged.passUnretained(self).toOpaque()
        rt.supports_selection_clipboard = true
        rt.wakeup_cb = { _ in GhosttyApp.shared.scheduleTick() }
        rt.action_cb = { _, target, action in GhosttyApp.shared.handleAction(target, action) }
        rt.read_clipboard_cb = { ud, loc, state, mimes, count, list in
            GhosttyApp.readClipboard(ud, loc, state, LinuxClipboardAdapter.copyMimes(mimes, count: count), list)
        }
        rt.confirm_read_clipboard_cb = { ud, confirm, state, request in
            GhosttyApp.confirmReadClipboard(ud, confirm, state, request)
        }
        rt.write_clipboard_cb = { ud, loc, content, len, confirm in
            GhosttyApp.writeClipboard(ud, LinuxClipboardAdapter.copyItems(content, count: len), loc, confirm)
        }
        rt.close_surface_cb = { ud, _ in
            // not "close": on a plain exit SHOW_CHILD_EXITED logs that first, and each kind logs once
            guard let retained = RetainedGhosttySurface(ud, kind: "close_surface") else { return }
            GhosttyApp.queueProcessExit(retained)
        }

        app = ghostty_app_new(&rt, cfg)
        // libghostty starts with a light conditional state. Re-side and re-apply the app config before
        // any surface mounts; otherwise a dark launch rebuilds each surface from config files and drops
        // host-only env vars, initial input, and command fields from its surface configuration.
        if let app {
            ghostty_app_set_color_scheme(
                app, appearanceSide.isDark ? GHOSTTY_COLOR_SCHEME_DARK : GHOSTTY_COLOR_SCHEME_LIGHT)
            ghostty_app_update_config(app, cfg)
        }
        appliedAppearanceSide = appearanceSide
        ghostty_config_free(cfg)
    }

    /// The caller must tear down all window surfaces first. Retained config paths stay valid through
    /// ghostty_app_free, including any final conditional reload; queued ticks then see a nil app.
    /// A quarantined surface was never freed and is still registered with the app, and App.zig `deinit` frees every
    /// registered surface, so freeing the app would free the one engine the quarantine exists to keep alive. The app
    /// and its config paths are then left to process exit, like the quarantined engine itself.
    @MainActor func shutdown() {
        guard !isShutDown else { return }
        isShutDown = true
        guard gSurfaceQuarantine.count == 0 else {
            FileHandle.standardError.write(Data("agterm: libghostty app left to process exit: a surface is quarantined\n".utf8))
            return
        }
        if let app {
            self.app = nil
            ghostty_app_free(app)
        }
        if case .success(let storage) = generatedFiles {
            do { try storage.cleanup() } catch { Self.reportConfigFailure(error) }
        }
    }

    func tick() { if let a = app { ghostty_app_tick(a) } }

    func updateConfig(_ config: ghostty_config_t) {
        guard let app else { return }
        ghostty_app_update_config(app, config)
    }

    @MainActor func applyColorScheme(_ side: LinuxAppearanceSide) {
        guard let app else { return }
        ghostty_app_set_color_scheme(
            app, side.isDark ? GHOSTTY_COLOR_SCHEME_DARK : GHOSTTY_COLOR_SCHEME_LIGHT)
        appliedAppearanceSide = side
    }

    /// Build a ghostty config (bundled defaults + the user's ~/.config/ghostty + the given
    /// extra lines, e.g. `theme = <name>`), finalized and ready for ghostty_surface_update_config.
    /// Caller owns it and must ghostty_config_free it after applying.
    private(set) var staticTitle: String?

    func buildConfig(extraLines: [String]) -> ghostty_config_t? {
        guard !isShutDown, let cfg = ghostty_config_new() else { return nil }
        var ready = false
        defer { if !ready { ghostty_config_free(cfg) } }
        do {
            let storage = try generatedFiles.get()
            let term = GhosttyResourceResolver.terminalName(resolvedResources: resolvedResources)
            try storage.withConfigFile("term = \(term)\n" + GhosttyDefaults.baseConfLines, as: .defaults) {
                $0.withCString { ghostty_config_load_file(cfg, $0) }
            }
            if linuxSettingsStore().load().inheritGlobalGhosttyConfig == true {
                ghostty_config_load_default_files(cfg)
            }
            // The scoped layer overrides bundled/global defaults; UI settings still load last.
            if let scoped = Self.scopedGhosttyConfigPath(), FileManager.default.fileExists(atPath: scoped) {
                scoped.withCString { ghostty_config_load_file(cfg, $0) }
            }
            // Empty themes preserve conditional color-scheme reports with agterm's fixed palette.
            // Only the immutable theme pair must survive conditional-state changes. Ordinary config
            // arguments have already been copied when load_file returns (pinned Config.loadIter/replay).
            let themes = try storage.appearanceThemes()
            let lines = ["theme = light:\(themes.light),dark:\(themes.dark)"] + extraLines
            try storage.withConfigFile(lines.joined(separator: "\n"), as: .runtime) {
                $0.withCString { ghostty_config_load_file(cfg, $0) }
            }
            // Includes expand after all layers and before finalization, matching macOS ordering.
            ghostty_config_load_recursive_files(cfg)
            let title = try clearStaticTitle(cfg, storage: storage)
            ghostty_config_finalize(cfg)
            staticTitle = title
            ready = true
            return cfg
        } catch {
            Self.reportConfigFailure(error)
            return nil
        }
    }

    /// A Ghostty static title would suppress the OSC title reports managed zmx uses for pane leadership.
    /// Keep the user's title at the host boundary and let the terminal report every role change.
    private func clearStaticTitle(_ config: ghostty_config_t, storage: GhosttyGeneratedConfigStorage) throws -> String? {
        let key = "title"
        var value: UnsafePointer<CChar>?
        let found = key.withCString { ghostty_config_get(config, &value, $0, UInt(key.utf8.count)) }
        let title = found ? value.map { String(cString: $0) }.flatMap { $0.isEmpty ? nil : $0 } : nil
        guard title != nil else { return nil }
        try storage.withConfigFile("title =\n", as: .title) {
            $0.withCString { ghostty_config_load_file(config, $0) }
        }
        return title
    }

    /// Build a config for one surface with a final per-session overlay (`background-image*`, solid
    /// `background`, and/or a font-size override). The returned config is owned by the caller.
    ///
    /// Resolves through the live theme-picker preview (hence `@MainActor`): a program's OSC 11 during a
    /// preview reapplies the overlay without explicit settings, so a persisted-settings base would
    /// rebuild the surface with the OLD theme's palette mid-preview.
    @MainActor
    func configWithOverlay(_ overlayText: String, settings: AppSettings? = nil) -> ghostty_config_t? {
        let base = AppController.ghosttyLines(
            for: settings ?? AppController.resolvedThemeSettings(persisted: linuxSettingsStore().load()),
            isDark: appliedAppearanceSide?.isDark ?? AppController.systemIsDark)
        let overlay = overlayText.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        return buildConfig(extraLines: base + overlay)
    }

    /// `<configDir>/ghostty.conf` — the agterm-scoped ghostty config layer, resolved via the shared
    /// ConfigPaths (honors a custom config dir + AGTERM_STATE_DIR isolation).
    private static func scopedGhosttyConfigPath() -> String? {
        let env = ProcessInfo.processInfo.environment
        let dir = ConfigPaths.configDirectory(setting: linuxSettingsStore().load().configDirectory,
                                               stateDir: env["AGTERM_STATE_DIR"],
                                               home: FileManager.default.homeDirectoryForCurrentUser)
        return ConfigPaths.ghosttyConfigPath(configDirectory: dir).path
    }

    private static func reportConfigFailure(_ error: Error) {
        FileHandle.standardError.write(Data(("agterm: \(error)\n").utf8))
    }

    /// Coalesce libghostty wakeups (fired off the main thread, faster than the
    /// loop drains) into a single queued main-thread `ghostty_app_tick`.
    func scheduleTick() {
        tickLock.lock()
        let already = tickScheduled
        tickScheduled = true
        tickLock.unlock()
        guard !already else { return }
        g_idle_add({ _ in
            let s = GhosttyApp.shared
            s.tickLock.lock(); s.tickScheduled = false; s.tickLock.unlock()
            MainActor.assumeIsolated { s.tick() }
            return 0 // G_SOURCE_REMOVE
        }, nil)
    }

    // MARK: - Action routing (runs on the main thread, during a tick)

    private func handleAction(_ target: ghostty_target_s, _ action: ghostty_action_s) -> Bool {
        // true, because a false OPEN_URL runs the engine's own opener; SHOW_CHILD_EXITED's answer only picks its message
        if Self.dropsQuarantined(target, action.tag) { return true }
        return MainActor.assumeIsolated {
            switch action.tag {
            case GHOSTTY_ACTION_RENDER:
                Self.wrapper(fromTarget: target)?.queueRender()
                return true
            case GHOSTTY_ACTION_SET_TITLE:
                guard let w = Self.wrapper(fromTarget: target), let ptr = action.action.set_title.title else { return true }
                w.applyTitle(String(cString: ptr))
                return true
            case GHOSTTY_ACTION_PWD:
                guard let w = Self.wrapper(fromTarget: target), let ptr = action.action.pwd.pwd else { return true }
                w.applyPwd(String(cString: ptr))
                return true
            case GHOSTTY_ACTION_SHOW_CHILD_EXITED:
                guard let w = Self.wrapper(fromTarget: target) else { return false }
                let retained = w.surface.flatMap { RetainedGhosttySurface(ghostty_surface_userdata($0)) }
                guard w.shouldCloseOnChildExitAction else {
                    w.handleHeldProcessExit()
                    // the held hook reaches the store, which may close and free this surface: never inside the callback
                    guard let retained else { return false }
                    runOnMain { MainActor.assumeIsolated {
                        retained.surface.onExitHeld?()
                        retained.release()
                    } }
                    return false
                }
                guard let retained else { return false }
                Self.queueProcessExit(retained)
                return true
            case GHOSTTY_ACTION_CELL_SIZE:
                // fires when the cell pixel size changes (font-size via Ctrl+/-, or a DPI change):
                // a trigger to read + persist the live font size.
                Self.wrapper(fromTarget: target)?.reportFontSize()
                return true
            case GHOSTTY_ACTION_MOUSE_VISIBILITY:
                // hide the pointer over the terminal while typing; restore it on movement.
                Self.wrapper(fromTarget: target)?.setMouseVisible(action.action.mouse_visibility == GHOSTTY_MOUSE_VISIBLE)
                return true
            case GHOSTTY_ACTION_RING_BELL:
                if let display = gdk_display_get_default() { gdk_display_beep(display) }
                return true
            case GHOSTTY_ACTION_OPEN_URL:
                let value = action.action.open_url
                if let raw = GhosttyActionDecoder.utf8String(value.url, length: value.len) {
                    if Self.wrapper(fromTarget: target)?.openLink(raw) != true { Self.openTerminalURL(raw) }
                }
                return true
            case GHOSTTY_ACTION_MOUSE_OVER_LINK:
                // a non-empty url means the pointer is over a hyperlink → show the hand cursor.
                // libghostty signals hover-clear with an EMPTY url (len == 0); the pointer is never NULL.
                let action_ = action.action.mouse_over_link
                Self.wrapper(fromTarget: target)?.setLinkHover(LinkHoverDecision.isActive(url: action_.url, len: action_.len))
                return true
            case GHOSTTY_ACTION_MOUSE_SHAPE:
                Self.wrapper(fromTarget: target)?.setMouseShape(action.action.mouse_shape)
                return true
            case GHOSTTY_ACTION_DESKTOP_NOTIFICATION:
                let n = action.action.desktop_notification
                let title = n.title.map { String(cString: $0) } ?? ""
                let body = n.body.map { String(cString: $0) } ?? ""
                if let origin = Self.wrapper(fromTarget: target)?.terminalNotificationOrigin() {
                    routeTerminalNotification(origin: origin, title: title, body: body)
                } else {
                    NotificationManager.send(title: title, body: body)
                }
                return true
            case GHOSTTY_ACTION_START_SEARCH:
                Self.wrapper(fromTarget: target)?.applySearchStart(action.action.start_search.needle.flatMap { String(cString: $0) })
                return true
            case GHOSTTY_ACTION_END_SEARCH:
                Self.wrapper(fromTarget: target)?.applySearchEnd()
                return true
            case GHOSTTY_ACTION_SEARCH_TOTAL:
                let raw = action.action.search_total.total
                Self.wrapper(fromTarget: target)?.applySearchTotal(raw < 0 ? nil : Int(raw))
                return true
            case GHOSTTY_ACTION_SEARCH_SELECTED:
                let raw = action.action.search_selected.selected
                Self.wrapper(fromTarget: target)?.applySearchSelected(raw < 0 ? nil : Int(raw))
                return true
            case GHOSTTY_ACTION_PROGRESS_REPORT:
                // OSC 9;4 (ConEmu/agent long-task progress). nil = clear, -1 = indeterminate, 0-100 = percent.
                let r = action.action.progress_report
                let value: Int?
                if r.state == GHOSTTY_PROGRESS_STATE_REMOVE {
                    value = nil
                } else if r.state == GHOSTTY_PROGRESS_STATE_INDETERMINATE {
                    value = -1
                } else {
                    value = r.progress < 0 ? -1 : Int(r.progress)
                }
                Self.wrapper(fromTarget: target)?.applyProgress(value)
                return true
            case GHOSTTY_ACTION_RELOAD_CONFIG:
                _ = GhosttyReloadPolicy.handle(isSoft: action.action.reload_config.soft) {
                    gWindows.values.first?.reloadConfig()
                }
                return true
            case GHOSTTY_ACTION_CONFIG_CHANGE:
                // ghostty reloaded its config (e.g. an external edit it picked up) — re-tint the sidebar in
                // case the theme changed; the surface re-renders itself.
                Self.wrapper(fromTarget: target)?.applyHostConfigChange()
                return true
            case GHOSTTY_ACTION_COLOR_CHANGE:
                let change = action.action.color_change
                if change.kind == GHOSTTY_ACTION_COLOR_KIND_BACKGROUND {
                    Self.wrapper(fromTarget: target)?.applyOSCBackground(
                        red: change.r, green: change.g, blue: change.b)
                }
                return true
            default:
                return false
            }
        }
    }

    #if DEBUG
    /// Drive the real libghostty action boundary with a non-NUL-terminated, sentinel-suffixed URL.
    /// The isolated AT-SPI runtime uses this when compositor pointer injection cannot express link hover reliably.
    @MainActor static func exerciseURLAction(_ raw: String) {
        var bytes = raw.utf8.map { CChar(bitPattern: $0) }
        bytes.append(contentsOf: [CChar(bitPattern: 0x58), CChar(bitPattern: 0x59)])
        bytes.withUnsafeMutableBufferPointer { buffer in
            var payload = ghostty_action_u()
            payload.open_url = ghostty_action_open_url_s(
                kind: GHOSTTY_ACTION_OPEN_URL_KIND_TEXT,
                url: UnsafePointer(buffer.baseAddress),
                len: UInt(raw.utf8.count)
            )
            let action = ghostty_action_s(tag: GHOSTTY_ACTION_OPEN_URL, action: payload)
            _ = shared.handleAction(ghostty_target_s(), action)
        }
    }
    #endif

    /// Apply the shared safe-link policy. Linux has no portable select-in-file-manager API, so a
    /// local file reveal opens its containing directory with the desktop's default file manager.
    static func openTerminalURL(_ raw: String) {
        let uri: String
        switch LinkPolicy.disposition(for: raw) {
        case .open(let url):
            uri = url.absoluteString
        case .reveal(let url), .image(let url), .markdown(let url), .text(let url, _), .document(let url):
            uri = url.deletingLastPathComponent().absoluteString
        case .ignore:
            return
        }
        #if DEBUG
        if let capturePath = ProcessInfo.processInfo.environment["AGTERM_ATSPI_URL_CAPTURE"],
           !capturePath.isEmpty {
            try? uri.write(toFile: capturePath, atomically: true, encoding: .utf8)
        }
        #endif
        launchDefaultHandler(forURI: uri)
    }

    // MARK: - Clipboard (GTK4 reads are async)
    //
    // libghostty fires these from `ghostty_app_tick` and from the surface input calls, all on the GTK main
    // thread, so GDK is safe here and each callback's result can be decided before it returns.

    private static func readClipboard(_ ud: UnsafeMutableRawPointer?, _ loc: ghostty_clipboard_e, _ state: UnsafeMutableRawPointer?,
                                      _ mimes: [String], _ list: Bool) -> ghostty_clipboard_read_result_e {
        nonisolated(unsafe) let userdata = ud
        nonisolated(unsafe) let requestState = state
        return MainActor.assumeIsolated {
            guard let surface = surface(from: userdata) else { return GHOSTTY_CLIPBOARD_READ_UNSUPPORTED }
            // A drop armed by `pasteThroughProtection` answers this read itself, unconfirmed, so libghostty
            // applies the same paste protection as Ctrl+Shift+V (`ghostty_surface_text` would bypass it).
            if let dropped = ArmedDropPaste.shared.take(owner: userdata) {
                // LinuxPastePolicy already turned a dropped uri-list into shell-escaped paths before arming.
                let completion = LinuxClipboardPolicy.textCompletion(text: dropped, list: list)
                LinuxClipboardAdapter.complete(surface, items: completion.items, available: completion.available,
                                               state: requestState)
                return GHOSTTY_CLIPBOARD_READ_STARTED
            }
            guard let clipboard = LinuxClipboardAdapter.gdkClipboard(for: loc) else {
                return GHOSTTY_CLIPBOARD_READ_UNSUPPORTED
            }
            switch LinuxClipboardPolicy.readPlan(mimes: mimes, list: list) {
            case .unavailable:
                return GHOSTTY_CLIPBOARD_READ_UNAVAILABLE
            case .listOnly:
                let available = LinuxClipboardPolicy.listing(hasText: LinuxClipboardAdapter.advertisesText(clipboard))
                LinuxClipboardAdapter.complete(surface, items: [], available: available, state: requestState)
                return GHOSTTY_CLIPBOARD_READ_STARTED
            case .text(let wantsList):
                guard let retained = RetainedGhosttySurface(userdata) else { return GHOSTTY_CLIPBOARD_READ_UNSUPPORTED }
                // Taken as the read starts, from the offer GDK reads.
                let read = ClipboardTextRead(state: requestState, retained: retained, list: wantsList,
                                             advertisesURIList: LinuxClipboardAdapter.advertisesURIList(clipboard))
                LinuxClipboardAdapter.readText(clipboard, read)
                return GHOSTTY_CLIPBOARD_READ_STARTED
            }
        }
    }

    private static func confirmReadClipboard(_ ud: UnsafeMutableRawPointer?, _ confirm: UnsafePointer<ghostty_clipboard_confirm_s>?,
                                             _ state: UnsafeMutableRawPointer?, _ request: ghostty_clipboard_request_e) {
        nonisolated(unsafe) let userdata = ud
        nonisolated(unsafe) let requestState = state
        let payload = confirm.map { LinuxClipboardAdapter.copyConfirmation($0.pointee) }
        MainActor.assumeIsolated {
            guard let surface = surface(from: userdata) else { return }
            guard let payload else {
                ghostty_surface_deny_clipboard_request(surface, requestState)
                return
            }
            // libghostty raises a paste here only when it judged it unsafe (multi-line or a bracketed-paste
            // end marker without bracketed mode): show the text and ask, never wave it through.
            if request == GHOSTTY_CLIPBOARD_REQUEST_PASTE {
                guard let retained = RetainedGhosttySurface(userdata) else { return }
                let text = LinuxUnsafePasteText.text(of: payload.items)
                runOnMain {
                    MainActor.assumeIsolated {
                        let requester = retained.surface
                        guard requester.surface != nil else {
                            retained.release()
                            return
                        }
                        UnsafePasteController.shared.confirm(text, request: requestState, on: requester) {
                            retained.release()
                        }
                    }
                }
                return
            }
            // a kind this revision does not know is refused, never completed unprompted
            guard let kind = LinuxClipboardAdapter.requestKind(request) else {
                ghostty_surface_deny_clipboard_request(surface, requestState)
                return
            }
            guard let access = LinuxClipboardPolicy.access(for: kind) else {
                LinuxClipboardAdapter.complete(surface, items: payload.items, available: payload.available,
                                               state: requestState, confirmed: true)
                return
            }
            guard let retained = RetainedGhosttySurface(userdata) else { return }
            // a modal prompt inside this callback would reenter the tick that fired it
            runOnMain {
                MainActor.assumeIsolated {
                    let requester = retained.surface
                    guard requester.surface != nil else {
                        retained.release()
                        return
                    }
                    let answer = LinuxClipboardAdapter.confirmationAnswer(liveSurface: { requester.surface }, payload: payload,
                                                                          state: requestState)
                    ClipboardPromptController.shared.request(access, requester: requester) { allowed in
                        defer { retained.release() }
                        answer(allowed)
                    }
                }
            }
        }
    }

    private static func writeClipboard(_ ud: UnsafeMutableRawPointer?, _ items: [ClipboardItem], _ loc: ghostty_clipboard_e, _ confirm: Bool) {
        guard !gSurfaceQuarantine.drops(quarantineKey(ud), kind: "clipboard write"), !items.isEmpty else { return }
        guard confirm else {
            // synchronous so a same-tick read sees it, which GDK allows only on its own thread
            assert(g_main_context_is_owner(g_main_context_default()) != 0, "clipboard write off the GTK main thread")
            LinuxClipboardAdapter.setClipboard(items, loc: loc)
            return
        }
        guard let retained = RetainedGhosttySurface(ud) else { return }
        runOnMain {
            MainActor.assumeIsolated {
                let requester = retained.surface
                guard requester.surface != nil else {
                    retained.release()
                    return
                }
                let answer = gSurfaceQuarantine.writeAnswer(key: retained.key, isLive: { MainActor.assumeIsolated { requester.surface != nil } }, write: {
                    LinuxClipboardAdapter.setClipboard(items, loc: loc)
                })
                ClipboardPromptController.shared.request(.write, requester: requester) { allowed in
                    defer { retained.release() }
                    answer(allowed)
                }
            }
        }
    }

    // MARK: - userdata recovery

    @MainActor static func surface(from ud: UnsafeMutableRawPointer?) -> ghostty_surface_t? {
        wrapper(from: ud)?.surface
    }
    static func wrapper(from ud: UnsafeMutableRawPointer?) -> GhosttySurface? {
        guard let ud, !gSurfaceQuarantine.drops(quarantineKey(ud), kind: "clipboard read") else { return nil }
        return Unmanaged<GhosttySurface>.fromOpaque(ud).takeUnretainedValue()
    }
    static func wrapper(fromTarget target: ghostty_target_s) -> GhosttySurface? {
        guard let ud = targetUserdata(target), !gSurfaceQuarantine.contains(quarantineKey(ud)) else { return nil }
        return Unmanaged<GhosttySurface>.fromOpaque(ud).takeUnretainedValue()
    }
    private static func targetUserdata(_ target: ghostty_target_s) -> UnsafeMutableRawPointer? {
        guard target.tag == GHOSTTY_TARGET_SURFACE, let surface = target.target.surface else { return nil }
        return ghostty_surface_userdata(surface)
    }
    static func quarantineKey(_ ud: UnsafeMutableRawPointer?) -> UInt {
        LinuxSurfaceQuarantine<GhosttySurfaceQuarantineRecord>.key(ud)
    }
    private static func dropsQuarantined(_ target: ghostty_target_s, _ tag: ghostty_action_tag_e) -> Bool {
        guard let ud = targetUserdata(target) else { return false }
        return gSurfaceQuarantine.drops(quarantineKey(ud), kind: actionName(tag))
    }
    private static func actionName(_ tag: ghostty_action_tag_e) -> String {
        switch tag {
        case GHOSTTY_ACTION_MOUSE_SHAPE: "MOUSE_SHAPE"
        case GHOSTTY_ACTION_SET_TITLE: "SET_TITLE"
        case GHOSTTY_ACTION_DESKTOP_NOTIFICATION: "DESKTOP_NOTIFICATION"
        case GHOSTTY_ACTION_SHOW_CHILD_EXITED: "close"   // a `command` surface always waits, so close_surface_cb never fires
        default: "action \(tag.rawValue)"
        }
    }

    /// The closing hop for both `close_surface_cb` and `SHOW_CHILD_EXITED`.
    private static func queueProcessExit(_ retained: RetainedGhosttySurface) {
        let exit = gSurfaceQuarantine.exitHop(key: retained.key) { MainActor.assumeIsolated { retained.surface.handleProcessExit() } }
        runOnMain { MainActor.assumeIsolated {
            exit()
            retained.release()
        } }
    }

    /// Resolve the ghostty resources dir (shell-integration + themes, with the compiled terminfo DB as its
    /// SIBLING) and export `GHOSTTY_RESOURCES_DIR`, so libghostty derives `TERMINFO=dirname(dir)/terminfo`
    /// and injects shell-integration at shell spawn (cwd/title reporting → OSC 7/133). Mirrors the macOS
    /// GhosttyApp.resolveResources(); candidates are the dev env override, the per-user install, then the
    /// system ghostty package.
    private func setGhosttyResourcesEnv() {
        let resolver = GhosttyResourceResolver(candidates: ghosttyResourceCandidates(),
                                               fileExists: { FileManager.default.fileExists(atPath: $0) })
        let resolved = resolver.resolve()
        resolvedResources = resolved
        if let dir = resolved {
            if dir == "/usr/share/ghostty" {
                FileHandle.standardError.write(Data("agterm: using system Ghostty resources at \(dir)\n".utf8))
            }
            setenv("GHOSTTY_RESOURCES_DIR", dir, 1)
        } else {
            unsetenv("GHOSTTY_RESOURCES_DIR")
        }
    }
}

/// Run a closure on the GTK main thread (callbacks may fire off-main).
func runOnMain(_ body: @escaping @Sendable () -> Void) {
    let box = Unmanaged.passRetained(MainClosure(body)).toOpaque()
    g_idle_add({ data in
        let c = Unmanaged<MainClosure>.fromOpaque(data!).takeRetainedValue()
        c.body()
        return 0
    }, box)
}
final class MainClosure: @unchecked Sendable { let body: @Sendable () -> Void; init(_ b: @escaping @Sendable () -> Void) { body = b } }

final class RetainedGhosttySurface: @unchecked Sendable {
    private let retained: Unmanaged<GhosttySurface>

    init?(_ ud: UnsafeMutableRawPointer?, kind: String = "close") {
        guard let ud, !gSurfaceQuarantine.drops(GhosttyApp.quarantineKey(ud), kind: kind) else { return nil }
        retained = Unmanaged<GhosttySurface>.fromOpaque(ud).retain()
    }

    var key: UInt { GhosttyApp.quarantineKey(retained.toOpaque()) }

    @MainActor var surface: GhosttySurface { retained.takeUnretainedValue() }

    func release() {
        retained.release()
    }
}
