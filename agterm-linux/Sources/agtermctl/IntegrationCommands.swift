import ArgumentParser
import Foundation
import LinuxIntegrations
import agtermCore

struct Integration: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Inspect or install local agterm integrations (no running app required).",
        subcommands: [Status.self, Install.self, Omarchy.self]
    )

    struct Omarchy: ParsableCommand {
        static let configuration = CommandConfiguration(
            abstract: "Manage agterm’s terminal tag in your Omarchy Hyprland configuration."
        )

        enum Action: String, ExpressibleByArgument {
            case enable, status, disable
        }

        @Argument(help: "Enable, inspect, or remove the per-user terminal tag.")
        var action: Action

        @Flag(name: .long, help: "Print the configuration status as JSON.")
        var json = false

        func run() throws {
            let integration = OmarchyTerminalIntegration()
            let result: OmarchyTerminalIntegration.Status
            switch action {
            case .enable: result = try integration.setEnabled(true)
            case .status: result = try integration.status()
            case .disable: result = try integration.setEnabled(false)
            }
            if json {
                try printJSON(result)
            } else {
                print("Omarchy terminal tag: \(result.state.rawValue)")
                print("Hyprland config: \(result.configPath)")
                print("Rule module: \(result.modulePath)")
            }
        }
    }

    struct Status: ParsableCommand {
        static let configuration = CommandConfiguration(abstract: "Show CLI, hooks, and skill status.")

        @Flag(name: .long, help: "Print a stable JSON status object.")
        var json = false

        @Option(name: .long, help: "Ignored compatibility option; integrations do not use the control socket.")
        var socket: String?

        func run() throws {
            let snapshot = IntegrationService().status()
            if json {
                try printJSON(snapshot)
                return
            }
            for item in snapshot.items {
                let path = item.path.map { " (\($0))" } ?? ""
                print("\(item.kind.title): \(item.state.label)\(path)")
                print("  \(item.detail)")
            }
        }
    }

    struct Install: ParsableCommand {
        static let configuration = CommandConfiguration(
            abstract: "Install or update hooks or the bundled agent skill."
        )

        @Argument(help: "Integration to install: hooks or skill.")
        var integration: String

        @Option(name: .long, help: "Choose the OpenCode plugin major when detection is unavailable: v1, v2, or skip.")
        var opencodeVersion: String?

        @Flag(name: .long, help: "Print the exact plan without changing files.")
        var dryRun = false

        @Flag(name: .long, help: "Print the plan or result as JSON.")
        var json = false

        @Option(name: .long, help: "Ignored compatibility option; integrations do not use the control socket.")
        var socket: String?

        func validate() throws {
            if let opencodeVersion, !["v1", "v2", "skip"].contains(opencodeVersion) {
                throw ValidationError("opencode-version must be v1, v2, or skip")
            }
            guard ["hooks", "skill"].contains(integration) else {
                throw ValidationError("integration must be hooks or skill")
            }
        }

        func run() throws {
            let service = IntegrationService(openCodeVersion: opencodeVersion.flatMap(AgentHooksInstall.OpenCode.Version.init(rawValue:)),
                                             skipOpenCode: opencodeVersion == "skip")
            let plan = try integration == "hooks" ? service.planHooks() : service.planSkill()
            if dryRun {
                if json { try printJSON(PlanPreview(plan)) } else { print(plan.summary) }
                if !plan.conflicts.isEmpty { throw ExitCode(rawValue: 2) }
                return
            }
            if !plan.canApply {
                if !plan.conflicts.isEmpty {
                    if json { try printJSON(PlanPreview(plan)) } else { writeError(plan.summary) }
                    throw ExitCode(rawValue: 2)
                }
                if json { try printJSON(PlanPreview(plan)) } else { print("No changes are needed.") }
                return
            }
            let result = try service.apply(plan)
            if json {
                try printJSON(result)
            } else {
                for operation in result.results {
                    print("\(operation.success ? "OK" : "FAILED") \(operation.action): \(operation.path)")
                    print("  \(operation.message)")
                }
                for conflict in result.conflicts { print("SKIPPED conflict: \(conflict)") }
            }
            if !result.results.allSatisfy(\.success) { throw ExitCode(rawValue: 4) }
            if !result.conflicts.isEmpty { throw ExitCode(rawValue: 2) }
        }
    }
}

private struct PlanPreview: Codable {
    let kind: IntegrationPlanKind
    let steps: [IntegrationPlanStep]
    let warnings: [String]
    let conflicts: [String]
    let canApply: Bool

    init(_ plan: IntegrationPlan) {
        kind = plan.kind
        steps = plan.steps
        warnings = plan.warnings
        conflicts = plan.conflicts
        canApply = plan.canApply
    }
}

private func printJSON<T: Encodable>(_ value: T) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(value)
    guard let text = String(data: data, encoding: .utf8) else { return }
    print(text)
}

private func writeError(_ text: String) {
    FileHandle.standardError.write(Data((text + "\n").utf8))
}
