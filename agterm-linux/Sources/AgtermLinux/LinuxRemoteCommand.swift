import Foundation
import agtermCore
#if canImport(Glibc)
import Glibc
#endif

enum LinuxRemoteCommand {
    static let treeDeadline: TimeInterval = 8
    private static let terminationGrace: TimeInterval = 0.25
    /// Per stream. Above Ghostty's default 10 MB scrollback so a full `zmx screen --all` still fits.
    static let outputLimit = 32 * 1024 * 1024
    static let outputLimitStatus: Int32 = 1

    static func run(_ argv: [String], deadline: TimeInterval, input: Data? = nil,
                    outputLimit: Int = outputLimit, temporaryDirectory: String = "/tmp",
                    inputCreate: (UnsafeMutablePointer<CChar>) -> Int32 = { mkstemp($0) },
                    inputWrite: (Int32, UnsafeRawPointer, Int) -> Int = { Glibc.write($0, $1, $2) }) -> RemoteCommandResult {
        guard !argv.isEmpty else {
            return RemoteCommandResult(status: 127, stdout: "", stderr: "no command to run")
        }
        let output = TemporaryOutput(directory: temporaryDirectory)
        let errors = TemporaryOutput(directory: temporaryDirectory)
        let inputFile = input.flatMap {
            TemporaryInput(data: $0, directory: temporaryDirectory, create: inputCreate, write: inputWrite)
        }
        guard let output, let errors else {
            return RemoteCommandResult(status: 1, stdout: "", stderr: "could not create command output files")
        }
        if input != nil, inputFile == nil {
            return RemoteCommandResult(status: 1, stdout: "", stderr: "could not create command input file")
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = argv
        process.environment = gdkEnvironment.restoringChildEnvironment(ProcessInfo.processInfo.environment)
        process.standardOutput = output.handle
        process.standardError = errors.handle
        if let inputFile { process.standardInput = inputFile.handle }
        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        do {
            try process.run()
        } catch {
            return RemoteCommandResult(status: 1, stdout: "", stderr: String(describing: error))
        }
        if finished.wait(timeout: .now() + deadline) == .timedOut {
            // Foundation spawns the child as a group leader and reports its exit only once every holder of an
            // inherited status socket has closed it, so a surviving descendant would hold this path open.
            let group = -process.processIdentifier
            _ = Glibc.kill(group, SIGTERM)
            if finished.wait(timeout: .now() + terminationGrace) == .timedOut {
                _ = Glibc.kill(group, SIGKILL)
                process.waitUntilExit()
            }
            output.closeForReading()
            errors.closeForReading()
            return RemoteCommandResult(status: 124, stdout: output.read(limit: outputLimit) ?? "",
                                       stderr: "remote command timed out")
        }
        output.closeForReading()
        errors.closeForReading()
        // Whole failure, not truncation: callers parse stdout, and a cut tree or screen would still parse.
        guard let stdout = output.read(limit: outputLimit), let stderr = errors.read(limit: outputLimit) else {
            return RemoteCommandResult(status: outputLimitStatus, stdout: "",
                                       stderr: "remote command output exceeded \(outputLimit) bytes")
        }
        return RemoteCommandResult(status: process.terminationStatus, stdout: stdout, stderr: stderr)
    }

}

private final class TemporaryInput {
    let path: String
    let handle: FileHandle

    init?(data: Data, directory: String, create: (UnsafeMutablePointer<CChar>) -> Int32,
          write: (Int32, UnsafeRawPointer, Int) -> Int) {
        var template = Array("\(directory)/agterm-input-XXXXXX".utf8CString)
        let descriptor = create(&template)
        guard descriptor >= 0 else { return nil }
        path = String(decoding: template.dropLast().map { UInt8(bitPattern: $0) }, as: UTF8.self)
        guard data.withUnsafeBytes({ raw in
            guard let base = raw.baseAddress else { return true }
            var offset = 0
            while offset < data.count {
                let count = write(descriptor, base + offset, data.count - offset)
                if count < 0, errno == EINTR { continue }
                guard count > 0 else { return false }
                offset += count
            }
            return true
        }) else {
            Glibc.close(descriptor)
            unlink(path)
            return nil
        }
        _ = lseek(descriptor, 0, SEEK_SET)
        handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    deinit {
        try? handle.close()
        try? FileManager.default.removeItem(atPath: path)
    }
}

private final class TemporaryOutput {
    let path: String
    let handle: FileHandle

    init?(directory: String) {
        var template = Array("\(directory)/agterm-remote-XXXXXX".utf8CString)
        let descriptor = mkstemp(&template)
        guard descriptor >= 0 else { return nil }
        path = String(decoding: template.dropLast().map { UInt8(bitPattern: $0) }, as: UTF8.self)
        handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }

    deinit {
        try? handle.close()
        try? FileManager.default.removeItem(atPath: path)
    }

    func closeForReading() {
        try? handle.synchronize()
        try? handle.close()
    }

    /// nil when the file holds more than `limit` bytes; never reads more than one byte past it.
    func read(limit: Int) -> String? {
        guard let reader = FileHandle(forReadingAtPath: path) else { return "" }
        defer { try? reader.close() }
        guard let data = (try? reader.read(upToCount: limit + 1)) ?? nil else { return "" }
        return data.count > limit ? nil : String(decoding: data, as: UTF8.self)
    }
}
