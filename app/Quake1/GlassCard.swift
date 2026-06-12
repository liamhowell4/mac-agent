import SwiftUI

/// The liquid-glass surface shared by every Quake panel. On macOS 26+ (Tahoe)
/// this uses the real Liquid Glass material; on older systems it falls back to a
/// layered ultra-thin material with a specular top highlight and a graded edge
/// so it still reads as glass rather than flat frosting.
struct GlassCard: ViewModifier {
    var cornerRadius: CGFloat = 28
    var tintedBorder: Color? = nil

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        if #available(macOS 26.0, *) {
            content
                .glassEffect(.regular, in: shape)
                .overlay(borderOverlay(shape))
                .shadow(color: .black.opacity(0.28), radius: 22, y: 8)
        } else {
            content
                .background(.ultraThinMaterial, in: shape)
                // Specular sheen: brighter at the top, fading down — the cue the
                // eye reads as a curved glass surface catching light.
                .overlay(
                    shape
                        .fill(
                            LinearGradient(
                                colors: [.white.opacity(0.22), .clear, .white.opacity(0.04)],
                                startPoint: .top,
                                endPoint: .bottom
                            )
                        )
                        .blendMode(.plusLighter)
                        .allowsHitTesting(false)
                )
                .overlay(borderOverlay(shape))
                .shadow(color: .black.opacity(0.35), radius: 24, y: 8)
        }
    }

    private func borderOverlay(_ shape: RoundedRectangle) -> some View {
        shape.strokeBorder(
            LinearGradient(
                colors: tintedBorder.map { [$0.opacity(0.6), $0.opacity(0.25)] }
                    ?? [.white.opacity(0.35), .white.opacity(0.08)],
                startPoint: .top,
                endPoint: .bottom
            ),
            lineWidth: 1
        )
    }
}

extension View {
    /// Apply the standard Quake liquid-glass surface.
    func glassCard(cornerRadius: CGFloat = 28, tintedBorder: Color? = nil) -> some View {
        modifier(GlassCard(cornerRadius: cornerRadius, tintedBorder: tintedBorder))
    }
}
