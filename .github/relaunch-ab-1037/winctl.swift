// Prints the number of on-screen, layer-0 windows owned by a pid, and the same count with off-screen ones
// included. Reads the window server only, so it never sends the app an event that could act as a reopen.
import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 2, let pid = Int32(CommandLine.arguments[1]) else {
    FileHandle.standardError.write("usage: winctl <pid>\n".data(using: .utf8)!)
    exit(2)
}

func count(_ options: CGWindowListOption) -> Int {
    let list = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
    return list.filter { info in
        guard (info[kCGWindowOwnerPID as String] as? Int32) == pid,
              (info[kCGWindowLayer as String] as? Int) == 0,
              let bounds = info[kCGWindowBounds as String] as? [String: Any],
              let height = bounds["Height"] as? Double, let width = bounds["Width"] as? Double
        else { return false }
        return width >= 100 && height >= 100
    }.count
}

print(count([.optionOnScreenOnly, .excludeDesktopElements]), count([.optionAll]))
