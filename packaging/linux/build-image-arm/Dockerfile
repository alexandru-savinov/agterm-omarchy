# Native aarch64 manifest of the official Swift 6.3.2 Ubuntu 24.04 image.
FROM swift:6.3.2-noble@sha256:b39f89a4f14b7bbb135dfb54df95b88c027e1190d366ed437d46d9560dcde3d0

LABEL org.opencontainers.image.title="agterm Omarchy Linux build environment" \
      org.opencontainers.image.description="Swift 6.3.2, Zig 0.16.0 and GTK build/test dependencies; not an agterm runtime image" \
      org.opencontainers.image.source="https://github.com/alexandru-savinov/agterm-omarchy" \
      org.opencontainers.image.url="https://github.com/alexandru-savinov/agterm-omarchy" \
      org.opencontainers.image.base.name="docker.io/library/swift:6.3.2-noble" \
      org.opencontainers.image.base.digest="sha256:b39f89a4f14b7bbb135dfb54df95b88c027e1190d366ed437d46d9560dcde3d0"

RUN test "$(uname -m)" = aarch64 \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev libepoxy-dev pkg-config \
       git curl ca-certificates xz-utils ncurses-bin ripgrep zsh gcc file \
       at-spi2-core dbus-x11 gir1.2-atspi-2.0 openbox python3-gi xdotool xvfb xauth \
       libatk-bridge2.0-0 libgl1-mesa-dri libegl-mesa0 mesa-utils imagemagick \
       desktop-file-utils shellcheck \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
      --retry 3 --output /tmp/zig.tar.xz \
      https://ziglang.org/download/0.16.0/zig-aarch64-linux-0.16.0.tar.xz \
    && echo 'ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17  /tmp/zig.tar.xz' | sha256sum --check \
    && tar -xJf /tmp/zig.tar.xz -C /opt \
    && rm /tmp/zig.tar.xz

ENV PATH="/opt/zig-aarch64-linux-0.16.0:${PATH}"
COPY --chmod=755 probe.sh /usr/local/bin/agterm-build-image-probe
# UID 1001 matches the GitHub-hosted runner user, so bind-mounted workspaces stay writable.
RUN useradd --create-home --uid 1001 --user-group builder \
    && install -d -o builder -g builder /workspace
USER builder
RUN agterm-build-image-probe
WORKDIR /workspace
CMD ["bash"]
