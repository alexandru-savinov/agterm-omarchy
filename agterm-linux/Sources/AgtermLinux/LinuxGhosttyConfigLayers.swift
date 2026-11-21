import CGtk

/// Load the theme colors before the scoped file while keeping ordinary Settings overrides after it.
struct LinuxGhosttyConfigLayers {
    let settings: [String]
    let omarchy: [String]
    var prefersAgtermTheme = false
    var overlay: [String] = []

    func load(
        into config: ghostty_config_t,
        storage: GhosttyGeneratedConfigStorage,
        scopedPath: String?,
        loadGlobal: (() -> Void)?
    ) throws {
        loadGlobal?()
        var runtime = settings
        if !omarchy.isEmpty {
            var theme = settings.filter(OmarchyTheme.isColorLine)
            runtime.removeAll(where: OmarchyTheme.isColorLine)
            if loadGlobal != nil {
                // The global Ghostty file on Omarchy includes its theme. Expand global includes before
                // the color layer, then clear the queue so they can't replay above the user's scoped file.
                ghostty_config_load_recursive_files(config)
                theme.insert("config-file =", at: 0)
            }
            if !prefersAgtermTheme { theme += omarchy }
            try storage.withConfigFile(theme.joined(separator: "\n"), as: .runtime) { path in
                path.withCString { ghostty_config_load_file(config, $0) }
            }
        }
        if let scopedPath { scopedPath.withCString { ghostty_config_load_file(config, $0) } }
        // Empty themes preserve conditional color-scheme reports with agterm's fixed palette.
        // The immutable pair must survive conditional-state changes. Ordinary arguments are copied.
        let themes = try storage.appearanceThemes()
        let lines = ["theme = light:\(themes.light),dark:\(themes.dark)"] + runtime + overlay
        try storage.withConfigFile(lines.joined(separator: "\n"), as: .runtime) { path in
            path.withCString { ghostty_config_load_file(config, $0) }
        }
        ghostty_config_load_recursive_files(config)
    }
}
