import Testing
@testable import AgtermLinux

@Suite("Software-rendering fallback")
struct LinuxSoftwareRenderingFallbackTests {
    typealias Fallback = LinuxSoftwareRenderingFallback

    @Test("reads LIBGL_ALWAYS_SOFTWARE the way Mesa does")
    func mesaBoolean() {
        for value in ["1", "true", "TRUE", "y", "yes"] { #expect(Fallback.requestsSoftware(value)) }
        for value in [nil, "", "0", "false", "no", "maybe"] { #expect(!Fallback.requestsSoftware(value)) }
    }

    @Test("relaunches a default-renderer launch with software rendering and the guard")
    func relaunchesDefaultLaunch() throws {
        let env = try #require(Fallback.relaunchEnvironment(preLaunch: ["PATH": "/usr/bin"], surfaceEverCreated: false))
        #expect(env["LIBGL_ALWAYS_SOFTWARE"] == "1")
        #expect(Fallback.isFallbackLaunch(env))
        #expect(env["PATH"] == "/usr/bin")
    }

    @Test("never relaunches twice, after a surface rendered, when disabled, or when software was already requested",
          arguments: [
              (["AGTERM_GL_SOFTWARE_FALLBACK": "1"], false),
              ([:], true),
              (["AGTERM_DISABLE_GL_FALLBACK": "1"], false),
              (["LIBGL_ALWAYS_SOFTWARE": "1"], false),
              (["LIBGL_ALWAYS_SOFTWARE": "true"], false),
          ])
    func declines(environment: [String: String], surfaceEverCreated: Bool) {
        #expect(Fallback.relaunchEnvironment(preLaunch: environment, surfaceEverCreated: surfaceEverCreated) == nil)
    }

    @Test("a relaunched image never relaunches again")
    func relaunchedImageDeclines() throws {
        let first = try #require(Fallback.relaunchEnvironment(preLaunch: [:], surfaceEverCreated: false))
        #expect(Fallback.relaunchEnvironment(preLaunch: first, surfaceEverCreated: false) == nil)
        var softwareStripped = first
        softwareStripped["LIBGL_ALWAYS_SOFTWARE"] = "0"
        #expect(Fallback.relaunchEnvironment(preLaunch: softwareStripped, surfaceEverCreated: false) == nil)
    }

    @Test("children of the relaunched image see neither variable when the caller had none")
    func childrenWithoutLauncherRecord() throws {
        let stale = ["AGTERM_ORIG_LD_LIBRARY_PATH": "", "AGTERM_ORIG_UNSET": "", "LD_LIBRARY_PATH": "/opt/lib"]
        let env = try #require(Fallback.relaunchEnvironment(preLaunch: stale, surfaceEverCreated: false))
        let child = LauncherChildEnvironment.sanitized(env)
        #expect(child["LIBGL_ALWAYS_SOFTWARE"] == nil)
        #expect(child["AGTERM_GL_SOFTWARE_FALLBACK"] == nil)
        #expect(child["LD_LIBRARY_PATH"] == "/opt/lib")
        #expect(!child.keys.contains { $0.hasPrefix("AGTERM_ORIG_") })
    }

    @Test("appends to the packaged launcher's record and restores the caller's own value")
    func childrenWithLauncherRecord() throws {
        let launched = [
            "AGTERM_ORIG_SET": "1",
            "AGTERM_ORIG_UNSET": "AGTERM_ZMX_PATH",
            "AGTERM_ORIG_LD_LIBRARY_PATH": "/caller/lib",
            "LD_LIBRARY_PATH": "/bundle/lib:/caller/lib",
            "AGTERM_ZMX_PATH": "/bundle/bin/zmx",
            "LIBGL_ALWAYS_SOFTWARE": "0",
        ]
        let env = try #require(Fallback.relaunchEnvironment(preLaunch: launched, surfaceEverCreated: false))
        #expect(env["AGTERM_ORIG_UNSET"] == "AGTERM_ZMX_PATH AGTERM_GL_SOFTWARE_FALLBACK")
        let child = LauncherChildEnvironment.sanitized(env)
        #expect(child["LIBGL_ALWAYS_SOFTWARE"] == "0")
        #expect(child["AGTERM_GL_SOFTWARE_FALLBACK"] == nil)
        #expect(child["AGTERM_ZMX_PATH"] == nil)
        #expect(child["LD_LIBRARY_PATH"] == "/caller/lib")
    }

    @Test("the hardware-only GL injection fails only off software rendering")
    func hardwareInjection() {
        let hardware = ["AGTERM_ATSPI_SCENARIO": "gl-fallback", "AGTERM_ATSPI_SURFACE_FAILURE": "gl-context-hardware:main"]
        var software = hardware
        software["LIBGL_ALWAYS_SOFTWARE"] = "1"
        #if DEBUG
        #expect(LinuxSurfaceFailureInjection.failure(for: .main, environment: hardware) == .glContext)
        #endif
        #expect(LinuxSurfaceFailureInjection.failure(for: .main, environment: software) == nil)
        #expect(LinuxSurfaceFailureInjection.failure(for: .split, environment: hardware) == nil)
    }
}
