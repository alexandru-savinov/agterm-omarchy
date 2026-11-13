# Native ARM build environment

This separate aarch64 counterpart prepares the GTK build/test environment for #375.
It does not change the existing x86 image or the production CI matrix.
SwiftLint remains in the existing x86 lint job; this GTK environment does not install it.

## Verified inputs

- Official `swift:6.3.2-noble` native Linux arm64/v8 manifest:
  `sha256:b39f89a4f14b7bbb135dfb54df95b88c027e1190d366ed437d46d9560dcde3d0`.
  Verified against the [official registry manifest](https://registry-1.docker.io/v2/library/swift/manifests/6.3.2-noble)
  and its content-addressed config (`os=linux`, `architecture=arm64`).
- Zig 0.16.0 aarch64 archive:
  `ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17`,
  from the [official release index](https://ziglang.org/download/index.json).
  The Docker build verifies downloaded archive bytes before extraction.

The base image supplies Ubuntu's signing keys; package installation retains APT signature verification.
The recipe installs GTK/WebKit, accessibility, X11, software Mesa and the existing test prerequisites.
Its final user is UID1001, matching GitHub-hosted workspace ownership.

## Native validation before publication

`publish-arm-build-image.yml` validates PRs on `ubuntu-24.04-arm`.
Both runner and image assert aarch64; there is no emulation setup.
The build runs the probe as the non-root image user, then a second container runs it with networking disabled,
a read-only root filesystem and a temporary writable `/tmp`.
The probe compiles and executes Swift/Foundation, Zig and GTK/libadwaita/WebKit programs,
initializes GTK on Xvfb and requires a software Mesa renderer.
That is software-rendering capability evidence, not full product UI acceptance.

## Separate publication and consumption gates

Publication requires a reviewed, explicit dispatch on this repository's main branch.
It uses the separate package `ghcr.io/alexandru-savinov/agterm-omarchy-build-aarch64`.
The source SHA, run ID and attempt form a unique tag; the workflow records the immutable digest and source labels
and attaches GitHub build provenance with narrowly scoped permissions.
Existing versions and x86 publication remain untouched.

Fresh native jobs must pull that digest with authenticated credentials and with an empty anonymous Docker config,
then execute the same probes before any separate CI matrix switch is proposed.
Before that switch, verify the published digest's GitHub attestation against this repository,
`publish-arm-build-image.yml`, the exact recorded source commit and `refs/heads/main`;
record the verification alongside both fresh pull/probe results.
A source label alone is not provenance verification.
A newly created GHCR package may be private; a failed anonymous pull remains a failed acceptance gate.
Do not substitute package metadata or an authenticated pull for public-consumption evidence.
Image publication, visibility changes and the later CI switch need coordinator review and are not part of PR validation.

The motivating successful ARM job spent360s on dependencies, including314s downloading214MB of archives.
An image can avoid that repeated provisioning, but its pull/probe overhead and net saving must be measured after integration.
Hosted ARM image validation is not native Omarchy application acceptance.
