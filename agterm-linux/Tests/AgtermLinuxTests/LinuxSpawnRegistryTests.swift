import Foundation
import Testing
import agtermCore
@testable import AgtermLinux

@MainActor
@Suite("Linux spawn registry grants for closed and reprioritized panes")
struct LinuxSpawnRegistryTests {
    @Test("canceled and deallocated panes never resume; the rest resume once in priority order")
    func cancelDeallocatePrioritize() {
        withManualTimer { fire in
            let keys = (0..<5).map { _ in UUID() }
            let resumed = ResumeLog()
            let registry = LinuxSpawnRegistry(pacer: SpawnPacer(interval: .zero, mainTimer: MainTimer.self))
            registry.pacer.arm(order: keys, burst: [])
            var surfaces: [FakePacedSurface?] = keys.map { _ in FakePacedSurface(log: resumed) }
            for (surface, key) in zip(surfaces, keys) {
                registry.enqueue(surface!, key: key, shouldPace: true)
                surface!.requestSpawn()
            }
            #expect(surfaces.allSatisfy { $0!.awaitingSpawnPermit })

            registry.cancel(keys[1])
            weak var deallocated = surfaces[2]
            surfaces[2] = nil
            #expect(deallocated == nil)
            registry.prioritize([surfaces[4]!, surfaces[3]!])
            #expect(resumed.keys.isEmpty)

            fire()
            #expect(resumed.keys == [keys[4], keys[3], keys[0]])
            #expect(registry.registeredKeys.isEmpty)
            #expect(registry.pacer.isPassthrough)
            fire()
            #expect(resumed.keys == [keys[4], keys[3], keys[0]])
        }
    }

    @Test("an unpaced pane is discarded without registering")
    func unpacedPane() {
        withManualTimer { fire in
            let key = UUID()
            let resumed = ResumeLog()
            let registry = LinuxSpawnRegistry(pacer: SpawnPacer(interval: .zero, mainTimer: MainTimer.self))
            registry.pacer.arm(order: [key], burst: [])
            let surface = FakePacedSurface(log: resumed)
            registry.enqueue(surface, key: key, shouldPace: false)
            fire()
            #expect(registry.registeredKeys.isEmpty)
            #expect(surface.spawnKey == nil)
            #expect(registry.pacer.isPassthrough)
            #expect(resumed.keys.isEmpty)
        }
    }

    /// Swaps the process-global timer seam for a queue the test drains; `fire` runs until nothing is queued.
    private func withManualTimer(_ body: (_ fire: () -> Void) -> Void) {
        let original = MainTimer.scheduleTimer
        defer { MainTimer.scheduleTimer = original }
        let queue = TimerQueue()
        MainTimer.scheduleTimer = { _, fire in
            queue.fires.append(fire)
            return {}
        }
        body {
            while !queue.fires.isEmpty { queue.fires.removeFirst()() }
        }
    }
}

@MainActor
private final class TimerQueue {
    var fires: [@MainActor () -> Void] = []
}

@MainActor
private final class ResumeLog {
    var keys: [UUID] = []
}

@MainActor
private final class FakePacedSurface: LinuxPacedSpawnSurface {
    private(set) var spawnKey: UUID?
    private(set) var awaitingSpawnPermit = false
    private var pacer: SpawnPacer?
    private let log: ResumeLog

    init(log: ResumeLog) { self.log = log }

    func useSpawnPacer(_ pacer: SpawnPacer, key: UUID) {
        self.pacer = pacer
        spawnKey = key
    }

    /// What `GhosttySurface.createSurface` does before it builds a surface.
    func requestSpawn() {
        guard let pacer, let spawnKey else { return }
        awaitingSpawnPermit = !pacer.request(spawnKey)
    }

    func resumePacedSpawn() {
        guard awaitingSpawnPermit, let spawnKey else { return }
        awaitingSpawnPermit = false
        log.keys.append(spawnKey)
    }
}
