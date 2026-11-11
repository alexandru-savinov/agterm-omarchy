import Foundation
import Testing
@testable import LinuxIntegrations

extension IntegrationServiceTests {
    @Test("existing ancestor aliases validate before their shared skill changes", arguments: [false, true])
    func existingAgentRootSkillAliases(existingSkill: Bool) throws {
        let fixture = try Fixture()
        let source = fixture.resources.appendingPathComponent("agent-skill")
        try fixture.write("<!-- agterm-skill -->\ncurrent", to: source.appendingPathComponent("SKILL.md"))
        let shared = fixture.root.appendingPathComponent("shared-agent-home", isDirectory: true)
        let skills = shared.appendingPathComponent("skills", isDirectory: true)
        let installed = skills.appendingPathComponent("agterm/SKILL.md")
        try FileManager.default.createDirectory(at: skills, withIntermediateDirectories: true)
        if existingSkill {
            try fixture.write("<!-- agterm-skill -->\nold", to: installed)
        }
        try FileManager.default.removeItem(at: fixture.home.appendingPathComponent(".codex"))
        for agent in [".claude", ".codex"] {
            try FileManager.default.createSymbolicLink(
                atPath: fixture.home.appendingPathComponent(agent).path,
                withDestinationPath: shared.path
            )
        }

        let service = fixture.service(path: [])
        let plan = try service.planSkill()
        #expect(plan.conflicts.isEmpty)
        #expect(plan.operations.count == 2)
        let result = try service.apply(plan)
        #expect(result.succeeded)
        #expect(result.results.allSatisfy { $0.success })
        #expect(try String(contentsOf: installed, encoding: .utf8) == "<!-- agterm-skill -->\ncurrent")
        for agent in [".claude", ".codex"] {
            #expect(try FileManager.default.destinationOfSymbolicLink(
                atPath: fixture.home.appendingPathComponent(agent).path
            ) == shared.path)
        }
        #expect(try service.planSkill().canApply == false)
    }

    @Test("retargeting an existing shared agent root rejects the preview before replacement")
    func retargetedExistingAgentRootSkillAlias() throws {
        let fixture = try Fixture()
        let source = fixture.resources.appendingPathComponent("agent-skill")
        try fixture.write("<!-- agterm-skill -->\ncurrent", to: source.appendingPathComponent("SKILL.md"))
        let shared = fixture.root.appendingPathComponent("shared-agent-home", isDirectory: true)
        let elsewhere = fixture.root.appendingPathComponent("other-agent-home", isDirectory: true)
        let installed = shared.appendingPathComponent("skills/agterm/SKILL.md")
        try fixture.write("<!-- agterm-skill -->\nold", to: installed)
        try fixture.write("<!-- agterm-skill -->\nother", to: elsewhere.appendingPathComponent("skills/agterm/SKILL.md"))
        try FileManager.default.removeItem(at: fixture.home.appendingPathComponent(".codex"))
        for agent in [".claude", ".codex"] {
            try FileManager.default.createSymbolicLink(
                atPath: fixture.home.appendingPathComponent(agent).path,
                withDestinationPath: shared.path
            )
        }
        let service = fixture.service(path: [])
        let plan = try service.planSkill()
        let codex = fixture.home.appendingPathComponent(".codex")
        try FileManager.default.removeItem(at: codex)
        try FileManager.default.createSymbolicLink(atPath: codex.path, withDestinationPath: elsewhere.path)

        #expect(throws: IntegrationServiceError.self) { try service.apply(plan) }
        #expect(try String(contentsOf: installed, encoding: .utf8) == "<!-- agterm-skill -->\nold")
    }
}
