import Testing
@testable import AgtermLinux

@Suite("AppImage child environment")
struct AppImageChildEnvironmentTests {
    @Test("removes AppImage library path from child environment")
    func removesBundledLibraryPath() {
        let environment = LauncherChildEnvironment.sanitized([
            "APPDIR": "/tmp/.mount_agterm/usr",
            "LD_LIBRARY_PATH": "/tmp/.mount_agterm/usr/lib",
            "PATH": "/tmp/.mount_agterm/usr/bin:/usr/bin"
        ])

        #expect(environment["LD_LIBRARY_PATH"] == nil)
        #expect(environment["APPDIR"] == "/tmp/.mount_agterm/usr")
        #expect(environment["PATH"] == "/tmp/.mount_agterm/usr/bin:/usr/bin")
    }

    @Test("preserves caller library paths when removing AppImage paths")
    func preservesCallerLibraryPath() {
        let environment = LauncherChildEnvironment.sanitized([
            "APPDIR": "/tmp/.mount_agterm",
            "LD_LIBRARY_PATH": "/tmp/.mount_agterm/usr/lib:/opt/vendor/lib:/usr/local/lib"
        ])

        #expect(environment["LD_LIBRARY_PATH"] == "/opt/vendor/lib:/usr/local/lib")
    }

    @Test("preserves environment outside AppImage")
    func preservesNativeEnvironment() {
        let environment = LauncherChildEnvironment.sanitized([
            "LD_LIBRARY_PATH": "/opt/lib",
            "PATH": "/usr/bin"
        ])

        #expect(environment["LD_LIBRARY_PATH"] == "/opt/lib")
        #expect(environment["PATH"] == "/usr/bin")
    }
}

/// The packaged launcher's bookkeeping (`scripts/stage-linux.sh`): `AGTERM_ORIG_<NAME>` for a variable the
/// caller had, `AGTERM_ORIG_UNSET` listing the ones it did not. Issue #270.
@Suite("Launcher child environment")
struct LauncherChildEnvironmentTests {
    /// What agterm's own environ looks like after the launcher ran, for a caller that had its own
    /// `LD_LIBRARY_PATH` but no renderer override and no `AGTERM_ZMX_PATH`.
    private let launched = [
        "LD_LIBRARY_PATH": "/opt/agterm/lib:/home/u/lib",
        "AGTERM_ORIG_LD_LIBRARY_PATH": "/home/u/lib",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "AGTERM_ZMX_PATH": "/opt/agterm/bin/zmx",
        "AGTERM_VERSION": "1.2.3",
        "AGTERM_ORIG_UNSET": "LIBGL_ALWAYS_SOFTWARE AGTERM_ZMX_PATH AGTERM_VERSION",
        "AGTERM_ORIG_SET": "1",
        "AGTERM_SOCKET": "/run/user/1/agterm.sock",
        "PATH": "/usr/bin",
    ]

    @Test("a variable the caller had gets the caller's value back")
    func restoresARecordedValue() {
        #expect(LauncherChildEnvironment.sanitized(launched)["LD_LIBRARY_PATH"] == "/home/u/lib")
    }

    @Test("a variable the caller never had is removed, not emptied")
    func removesAnUnsetVariable() {
        let child = LauncherChildEnvironment.sanitized(launched)
        #expect(!child.keys.contains("LIBGL_ALWAYS_SOFTWARE"))
        #expect(!child.keys.contains("AGTERM_ZMX_PATH"))
        #expect(!child.keys.contains("AGTERM_VERSION"))
    }

    @Test("the bookkeeping never reaches a child, and unrecorded variables pass through")
    func dropsBookkeepingOnly() {
        let child = LauncherChildEnvironment.sanitized(launched)
        #expect(child == ["LD_LIBRARY_PATH": "/home/u/lib", "AGTERM_SOCKET": "/run/user/1/agterm.sock",
                          "PATH": "/usr/bin"])
    }

    @Test("an empty recorded value is restored as empty, not removed")
    func restoresAnEmptyValue() {
        let child = LauncherChildEnvironment.sanitized([
            "LD_LIBRARY_PATH": "/opt/agterm/lib", "AGTERM_ORIG_LD_LIBRARY_PATH": "", "AGTERM_ORIG_UNSET": "",
            "AGTERM_ORIG_SET": "1",
        ])
        #expect(child == ["LD_LIBRARY_PATH": ""])
    }

    /// An agterm started from a pane WITHOUT the launcher (`scripts/run-linux.sh`) inherits the pane's
    /// blanked record, marker included. Reading it would hand every child `LD_LIBRARY_PATH=""`.
    @Test("a record whose marker is empty or missing restores nothing and is kept from children")
    func staleRecordFromAPaneIsIgnored() {
        let pane = [
            "LD_LIBRARY_PATH": "/home/u/lib", "LIBGL_ALWAYS_SOFTWARE": "", "AGTERM_ORIG_LD_LIBRARY_PATH": "",
            "AGTERM_ORIG_LIBGL_ALWAYS_SOFTWARE": "", "AGTERM_ORIG_UNSET": "LD_LIBRARY_PATH",
        ]
        let expected = ["LD_LIBRARY_PATH": "/home/u/lib", "LIBGL_ALWAYS_SOFTWARE": ""]
        #expect(LauncherChildEnvironment.sanitized(pane.merging(["AGTERM_ORIG_SET": ""]) { $1 }) == expected)
        #expect(LauncherChildEnvironment.sanitized(pane) == expected)
    }

    @Test("without bookkeeping nothing is restored")
    func absentBookkeepingIsANoOp() {
        let plain = ["LD_LIBRARY_PATH": "/opt/lib", "LIBGL_ALWAYS_SOFTWARE": "1", "AGTERM_ZMX_PATH": "/z"]
        #expect(LauncherChildEnvironment.restore(from: plain).isEmpty)
        #expect(LauncherChildEnvironment.sanitized(plain) == plain)
    }

    @Test("a recorded value wins over the same name listed as unset")
    func recordedValueWinsOverUnsetMarker() {
        let child = LauncherChildEnvironment.sanitized([
            "LD_LIBRARY_PATH": "/opt/agterm/lib", "AGTERM_ORIG_LD_LIBRARY_PATH": "/home/u/lib",
            "AGTERM_ORIG_UNSET": "LD_LIBRARY_PATH", "AGTERM_ORIG_SET": "1",
        ])
        #expect(child == ["LD_LIBRARY_PATH": "/home/u/lib"])
    }

    @Test("inside an AppImage the restored library path still loses the image's directories")
    func appImageFiltersTheRestoredValue() {
        let child = LauncherChildEnvironment.sanitized([
            "APPDIR": "/tmp/.mount_agterm",
            "LD_LIBRARY_PATH": "/tmp/.mount_agterm/opt/agterm/lib:/tmp/.mount_agterm/usr/lib:/home/u/lib",
            "AGTERM_ORIG_LD_LIBRARY_PATH": "/tmp/.mount_agterm/usr/lib:/home/u/lib", "AGTERM_ORIG_SET": "1",
        ])
        #expect(child == ["APPDIR": "/tmp/.mount_agterm", "LD_LIBRARY_PATH": "/home/u/lib"])

        let unset = LauncherChildEnvironment.sanitized([
            "APPDIR": "/tmp/.mount_agterm", "LD_LIBRARY_PATH": "/tmp/.mount_agterm/opt/agterm/lib",
            "AGTERM_ORIG_UNSET": "LD_LIBRARY_PATH", "AGTERM_ORIG_SET": "1",
        ])
        #expect(unset == ["APPDIR": "/tmp/.mount_agterm"])
    }

    /// The two helpers every spawning site uses. Reverting either to its GDK-only form reds this.
    @Test("both child-environment helpers carry the launcher restore")
    func preLaunchEnvironmentCarriesTheRestore() {
        let captured = LinuxGdkPolicy.PreLaunchEnvironment(gtkMajor: 4, gtkMinor: 22, environment: launched)

        let copied = captured.restoringChildEnvironment(launched.merging(["GDK_DISABLE": "gles-api,vulkan"]) { $1 })
        #expect(copied == ["LD_LIBRARY_PATH": "/home/u/lib", "AGTERM_SOCKET": "/run/user/1/agterm.sock",
                           "PATH": "/usr/bin", "GDK_DISABLE": ""])

        // Explicit values and deletions travel separately; per-pane keys still win.
        let surface = captured.childEnvironment(merging: ["AGTERM_PANE_ID": "p", "AGTERM_VERSION": "pane"])
        #expect(surface == [
            "LD_LIBRARY_PATH": "/home/u/lib", "AGTERM_VERSION": "pane", "GDK_DISABLE": "", "AGTERM_PANE_ID": "p",
        ])
        #expect(captured.childEnvironmentUnsets(merging: ["AGTERM_VERSION": "pane"]) ==
                ["AGTERM_ORIG_LD_LIBRARY_PATH", "AGTERM_ORIG_SET", "AGTERM_ORIG_UNSET",
                 "AGTERM_ZMX_PATH", "LIBGL_ALWAYS_SOFTWARE"])
    }

    @Test("surface restoration distinguishes absent, empty, and explicit caller overrides")
    func surfaceAbsenceAndEmptyAreDistinct() {
        let captured = LinuxGdkPolicy.PreLaunchEnvironment(gtkMajor: 4, gtkMinor: 12, environment: [
            "AGTERM_ORIG_SET": "1", "AGTERM_ORIG_UNSET": "LIBGL_ALWAYS_SOFTWARE",
            "AGTERM_ORIG_LD_LIBRARY_PATH": "", "LD_LIBRARY_PATH": "/bundle/lib",
        ])
        #expect(captured.childEnvironment(merging: [:]) == ["LD_LIBRARY_PATH": ""])
        #expect(captured.childEnvironmentUnsets(merging: [:]).contains("LIBGL_ALWAYS_SOFTWARE"))
        #expect(!captured.childEnvironmentUnsets(merging: [:]).contains("LD_LIBRARY_PATH"))
        #expect(!captured.childEnvironmentUnsets(merging: ["LIBGL_ALWAYS_SOFTWARE": ""])
            .contains("LIBGL_ALWAYS_SOFTWARE"))
        #expect(captured.childEnvironment(merging: ["LIBGL_ALWAYS_SOFTWARE": ""]) ==
                ["LD_LIBRARY_PATH": "", "LIBGL_ALWAYS_SOFTWARE": ""])
        #expect(captured.childEnvironment(merging: ["LD_LIBRARY_PATH": "/caller/lib"])["LD_LIBRARY_PATH"] == "/caller/lib")
    }
}
