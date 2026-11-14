import SwiftUI

/// Parent stacks probe every ZStack child at min, ideal and max sizes, hidden deck entries included.
/// The reader answers those probes itself and hands each terminal only the final size.
struct TerminalDeck<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        GeometryReader { geometry in
            ZStack { content }
                .frame(width: geometry.size.width, height: geometry.size.height)
        }
    }
}
