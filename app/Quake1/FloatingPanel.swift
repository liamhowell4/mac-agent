import AppKit
import SwiftUI

/// Borderless, non-activating floating panel that hosts the SwiftUI root view.
/// Spotlight-style: clear background (the SwiftUI view draws its own material
/// and shadow), joins all Spaces, hides on Esc and on losing key status.
final class FloatingPanel: NSPanel {
    static let panelWidth: CGFloat = 640

    /// Returns true while the daemon is mid-task (working, running a tool, or
    /// waiting on a confirm/ask). Set by AppDelegate. We refuse to auto-hide on
    /// resignKey in that window: a macOS permission (TCC) dialog steals key
    /// focus exactly then, and the panel vanishing out from under the prompt is
    /// the worst moment to disappear.
    var isBusy: () -> Bool = { false }

    init(rootView: AnyView) {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: Self.panelWidth, height: 600),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false // the SwiftUI capsule draws its own shadow
        // Drag to reposition like Spotlight: grabbing any non-interactive part
        // of the glass (icon area, padding, response card) moves the panel;
        // the text field and buttons still receive their own clicks.
        isMovableByWindowBackground = true
        hidesOnDeactivate = false
        isFloatingPanel = true
        becomesKeyOnlyIfNeeded = false

        let hosting = NSHostingView(rootView: rootView.frame(width: Self.panelWidth))
        hosting.sizingOptions = [.preferredContentSize]
        contentView = hosting
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    /// Esc.
    override func cancelOperation(_ sender: Any?) {
        hide()
    }

    override func resignKey() {
        super.resignKey()
        // Don't disappear while a task is in flight — losing key here usually
        // means a permission dialog (or the app the model is driving) just came
        // forward. Let it sit behind the dialog; Esc still closes it.
        if !isBusy() {
            hide()
        }
    }

    func hide() {
        orderOut(nil)
    }

    /// Position the panel horizontally centered, top edge ~30% down the screen.
    func present(centeredOn screen: NSScreen?) {
        guard let screen = screen ?? NSScreen.main else { return }
        layoutIfNeeded()
        let frame = self.frame
        let visible = screen.visibleFrame
        let x = visible.midX - frame.width / 2
        let y = visible.maxY - visible.height * 0.30 - frame.height
        setFrameOrigin(NSPoint(x: x, y: max(visible.minY, y)))
        orderFrontRegardless()
    }
}
