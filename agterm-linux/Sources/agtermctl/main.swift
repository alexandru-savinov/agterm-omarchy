import ArgumentParser
import Foundation
import Glibc
import agtermctlKit

struct AgtermctlLinux: ParsableCommand {
    static let configuration = AgtermctlCommandCatalog.rootConfiguration(
        abstract: "Drive agterm and manage local integrations.",
        appending: [Integration.self])
}

// The shared CLI defaults to agterm-server's socket; the desktop CLI meets the GTK app (`BasicOptions.socketPath`).
setenv("AGTERM_CLI_RENDEZVOUS", "desktop", 1)
AgtermctlLinux.mainWithTransportStatus()
