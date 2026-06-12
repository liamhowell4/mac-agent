import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var hotKey: HotKey?
    private var panel: FloatingPanel?

    let client = DaemonClient()
    let daemon = DaemonProcess()

    func applicationDidFinishLaunching(_ notification: Notification) {
        setUpStatusItem()
        setUpHotKey()
        setUpPanel()

        client.onConnectFailure = { [weak self] in
            self?.daemon.spawnIfNeeded()
        }
        // Eager spawn: the daemon exits immediately if another instance already
        // serves the socket, so this is safe and makes startup deterministic.
        daemon.spawnIfNeeded()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            self?.client.connect()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemon.terminate()
    }

    // MARK: - Status item

    private func setUpStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(
            systemSymbolName: "bolt.fill",
            accessibilityDescription: "Quake"
        )

        let menu = NSMenu()

        let showItem = NSMenuItem(
            title: "Show Quake (⌘⌃Space)",
            action: #selector(showFromMenu),
            keyEquivalent: ""
        )
        showItem.target = self
        menu.addItem(showItem)

        let newConvoItem = NSMenuItem(
            title: "New Conversation",
            action: #selector(newConversation),
            keyEquivalent: "n"
        )
        newConvoItem.target = self
        menu.addItem(newConvoItem)

        let restartItem = NSMenuItem(
            title: "Restart Daemon",
            action: #selector(restartDaemon),
            keyEquivalent: ""
        )
        restartItem.target = self
        menu.addItem(restartItem)

        menu.addItem(.separator())

        let quitItem = NSMenuItem(
            title: "Quit",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        menu.addItem(quitItem)

        item.menu = menu
        statusItem = item
    }

    // MARK: - Hotkey

    private func setUpHotKey() {
        hotKey = HotKey { [weak self] in
            self?.togglePanel()
        }
    }

    // MARK: - Panel

    private func setUpPanel() {
        let root = RootView()
            .environmentObject(client)
        panel = FloatingPanel(rootView: AnyView(root))
    }

    private func togglePanel() {
        guard let panel else { return }
        if panel.isVisible {
            panel.hide()
        } else {
            showPanel()
        }
    }

    private func showPanel() {
        guard let panel else { return }
        panel.present(centeredOn: activeScreen())
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
    }

    /// The screen the user is working on: prefer the one with the mouse pointer.
    private func activeScreen() -> NSScreen? {
        let mouse = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(mouse, $0.frame, false) }
            ?? NSScreen.main
    }

    // MARK: - Menu actions

    @objc private func showFromMenu() {
        showPanel()
    }

    @objc private func newConversation() {
        client.reset()
        showPanel()
    }

    @objc private func restartDaemon() {
        client.disconnect()
        daemon.restartDaemon()
        // Give the daemon a moment to bind the socket before reconnecting.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
            self?.client.connect()
        }
    }
}
