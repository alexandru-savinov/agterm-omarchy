# Omarchy support

This is the Omarchy build of agterm, based on the GTK Linux frontend.
Releases, their verification and what the native gates cover are described in the [README](README.md#releases-and-verification).

- Ask setup questions and share compatibility results in the [Omarchy compatibility discussion](https://github.com/alexandru-savinov/agterm-omarchy/discussions/7).
- Report a reproducible build, packaging, or desktop defect through the [Omarchy bug form](https://github.com/alexandru-savinov/agterm-omarchy/issues/new?template=bug.yml).
  Search existing issues first and include the commit or artifact, architecture, Omarchy/GTK/Hyprland versions, exact steps, expected/actual behavior, and sanitized logs.
- Discuss proposals in [Discussions](https://github.com/alexandru-savinov/agterm-omarchy/discussions), then use the [integration proposal form](https://github.com/alexandru-savinov/agterm-omarchy/issues/new?template=feature.yml) for an agreed task.
- To contribute code or agent work, follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [task ownership protocol](docs/AGENT_PROTOCOL.md).
- Send suspected vulnerabilities through the private route in [SECURITY.md](SECURITY.md).

Keep credentials, private terminal contents, and personal host details out of public attachments.
State whether a report came from an isolated test instance and whether it used native Wayland, XWayland, or X11.

For macOS builds and support, use [upstream agterm](https://github.com/umputun/agterm).
For shared GTK frontend issues, check [agterm-linux](https://github.com/melonamin/agterm-linux) and cross-link relevant existing reports rather than opening duplicates.
