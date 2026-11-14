import Foundation
import agtermCore

/// What the registry needs from a paced pane; `GhosttySurface` in the app, a fixture in tests.
@MainActor
protocol LinuxPacedSpawnSurface: AnyObject {
    var spawnKey: UUID? { get }
    var awaitingSpawnPermit: Bool { get }
    func useSpawnPacer(_ pacer: SpawnPacer, key: UUID)
    func resumePacedSpawn()
}

extension GhosttySurface: LinuxPacedSpawnSurface {}

/// Routes host-free spawn-pacer grants back to GTK surfaces without retaining closed panes.
@MainActor
final class LinuxSpawnRegistry {
    let pacer: SpawnPacer
    private struct Entry { weak var surface: (any LinuxPacedSpawnSurface)? }
    private var entries: [UUID: Entry] = [:]

    var registeredKeys: Set<UUID> { Set(entries.keys) }

    init(pacer: SpawnPacer = SpawnPacer(mainTimer: MainTimer.self)) {
        self.pacer = pacer
        pacer.onGrant = { [weak self] key in self?.grant(key) }
    }

    func enqueue(_ surface: any LinuxPacedSpawnSurface, key: UUID, shouldPace: Bool) {
        guard shouldPace else {
            pacer.discard(key)
            return
        }
        entries[key] = Entry(surface: surface)
        surface.useSpawnPacer(pacer, key: key)
    }

    private func grant(_ key: UUID) {
        guard let surface = entries.removeValue(forKey: key)?.surface,
              surface.awaitingSpawnPermit else { return }
        surface.resumePacedSpawn()
    }

    func prioritize(_ surfaces: [any LinuxPacedSpawnSurface]) {
        pacer.prioritize(surfaces.compactMap { $0.spawnKey })
    }

    func cancel(_ key: UUID) {
        entries[key] = nil
        pacer.cancel(key)
    }
}

@MainActor let gSpawnRegistry = LinuxSpawnRegistry()
