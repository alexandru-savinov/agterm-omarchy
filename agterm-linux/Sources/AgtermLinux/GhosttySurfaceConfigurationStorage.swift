import CGtk
import Glibc

final class GhosttySurfaceConfigurationStorage {
    typealias Duplicate = (String) -> UnsafeMutablePointer<CChar>?
    typealias Deallocate = (UnsafeMutablePointer<CChar>) -> Void

    private(set) var workingDirectory: UnsafeMutablePointer<CChar>?
    private(set) var command: UnsafeMutablePointer<CChar>?
    private(set) var initialInput: UnsafeMutablePointer<CChar>?
    private(set) var environment: UnsafeMutableBufferPointer<ghostty_env_var_s>?
    private(set) var environmentUnsets: UnsafeMutableBufferPointer<UnsafePointer<CChar>?>?
    private var strings: [UnsafeMutablePointer<CChar>] = []
    private let deallocate: Deallocate
    private(set) var isReleased = false

    init?(workingDirectory: String, command: String?, initialInput: String?, environment: [String: String],
          environmentUnsets: [String] = [],
          duplicate: Duplicate = { strdup($0) }, deallocate: @escaping Deallocate = { free($0) }) {
        self.deallocate = deallocate
        guard let workingDirectory = retain(workingDirectory, duplicate: duplicate) else {
            release()
            return nil
        }
        self.workingDirectory = workingDirectory
        if let command {
            guard let retained = retain(command, duplicate: duplicate) else { release(); return nil }
            self.command = retained
        }
        if let initialInput {
            guard let retained = retain(initialInput, duplicate: duplicate) else { release(); return nil }
            self.initialInput = retained
        }

        var entries: [ghostty_env_var_s] = []
        for (key, value) in environment.sorted(by: { $0.key < $1.key }) {
            guard let key = retain(key, duplicate: duplicate),
                  let value = retain(value, duplicate: duplicate) else { release(); return nil }
            entries.append(ghostty_env_var_s(key: UnsafePointer(key), value: UnsafePointer(value)))
        }
        if !entries.isEmpty {
            let pointer = UnsafeMutablePointer<ghostty_env_var_s>.allocate(capacity: entries.count)
            for (index, entry) in entries.enumerated() { pointer.advanced(by: index).initialize(to: entry) }
            self.environment = UnsafeMutableBufferPointer(start: pointer, count: entries.count)
        }
        var names: [UnsafePointer<CChar>?] = []
        for name in environmentUnsets {
            guard !name.isEmpty, !name.contains("="), !name.utf8.contains(0),
                  let pointer = retain(name, duplicate: duplicate) else { release(); return nil }
            names.append(UnsafePointer(pointer))
        }
        if !names.isEmpty {
            let pointer = UnsafeMutablePointer<UnsafePointer<CChar>?>.allocate(capacity: names.count)
            for (index, name) in names.enumerated() { pointer.advanced(by: index).initialize(to: name) }
            self.environmentUnsets = UnsafeMutableBufferPointer(start: pointer, count: names.count)
        }
    }

    deinit {
        release()
    }

    func apply(to config: inout ghostty_surface_config_s) {
        config.working_directory = UnsafePointer(workingDirectory)
        config.command = UnsafePointer(command)
        config.initial_input = UnsafePointer(initialInput)
        config.env_vars = environment?.baseAddress
        config.env_var_count = environment?.count ?? 0
    }

    func release() {
        guard !isReleased else { return }
        isReleased = true
        if let environment {
            environment.baseAddress?.deinitialize(count: environment.count)
            environment.baseAddress?.deallocate()
            self.environment = nil
        }
        if let environmentUnsets {
            environmentUnsets.baseAddress?.deinitialize(count: environmentUnsets.count)
            environmentUnsets.baseAddress?.deallocate()
            self.environmentUnsets = nil
        }
        strings.forEach(deallocate)
        strings = []
        workingDirectory = nil
        command = nil
        initialInput = nil
    }

    private func retain(_ value: String, duplicate: Duplicate) -> UnsafeMutablePointer<CChar>? {
        guard let pointer = duplicate(value) else { return nil }
        strings.append(pointer)
        return pointer
    }
}
