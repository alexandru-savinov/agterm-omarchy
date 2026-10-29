# Native x86_64 manifest of the official Swift 6.3.2 Ubuntu 24.04 image.
FROM swift:6.3.2-noble@sha256:d02671b53bebdf3be9dd82f51d2912d462fc9a017535272a266480c016e27f07

LABEL org.opencontainers.image.title="agterm Omarchy Linux build environment" \
      org.opencontainers.image.description="Swift 6.3.2, Zig 0.16.0 and GTK build/test dependencies; not an agterm runtime image" \
      org.opencontainers.image.source="https://github.com/alexandru-savinov/agterm-omarchy" \
      org.opencontainers.image.url="https://github.com/alexandru-savinov/agterm-omarchy" \
      org.opencontainers.image.base.name="docker.io/library/swift:6.3.2-noble" \
      org.opencontainers.image.base.digest="sha256:d02671b53bebdf3be9dd82f51d2912d462fc9a017535272a266480c016e27f07"

RUN test "$(uname -m)" = x86_64 \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev libepoxy-dev pkg-config \
       git curl ca-certificates xz-utils ncurses-bin ripgrep zsh gcc file \
       at-spi2-core dbus-x11 gir1.2-atspi-2.0 openbox python3-gi xdotool xvfb xauth \
       libatk-bridge2.0-0 libgl1-mesa-dri libegl-mesa0 mesa-utils imagemagick \
       desktop-file-utils shellcheck unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
      --retry 3 --output /tmp/zig.tar.xz \
      https://ziglang.org/download/0.16.0/zig-x86_64-linux-0.16.0.tar.xz \
    && echo '70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00  /tmp/zig.tar.xz' | sha256sum --check \
    && tar -xJf /tmp/zig.tar.xz -C /opt \
    && rm /tmp/zig.tar.xz

RUN curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
      --retry 3 --output /tmp/swiftlint.zip \
      https://github.com/realm/SwiftLint/releases/download/0.65.0/swiftlint_linux_amd64.zip \
    && echo '79306a34e5c7cc55a220cd108cbb861dcad5f10138dcdf261e2624ae8b0a486b  /tmp/swiftlint.zip' | sha256sum --check \
    && unzip -q /tmp/swiftlint.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/swiftlint \
    && rm /tmp/swiftlint.zip

ENV PATH="/opt/zig-x86_64-linux-0.16.0:${PATH}" \
    LINUX_SOURCEKIT_LIB_PATH="/usr/lib/libsourcekitdInProc.so"
COPY --chmod=755 probe.sh /usr/local/bin/agterm-build-image-probe
RUN agterm-build-image-probe
WORKDIR /workspace
CMD ["bash"]
