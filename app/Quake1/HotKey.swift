import Carbon.HIToolbox
import Foundation

/// Global hotkey via Carbon RegisterEventHotKey — no Accessibility permission
/// needed (unlike CGEventTap). Hard-wired to ⌘⌃Space.
final class HotKey {
    private var hotKeyRef: EventHotKeyRef?
    private var eventHandlerRef: EventHandlerRef?
    private let handler: @MainActor () -> Void

    private static let signature: OSType = 0x514B4531 // 'QKE1'
    private static let hotKeyID: UInt32 = 1

    /// kVK_Space = 49, modifiers cmd+control.
    init(keyCode: UInt32 = UInt32(kVK_Space),
         modifiers: UInt32 = UInt32(cmdKey | controlKey),
         handler: @escaping @MainActor () -> Void) {
        self.handler = handler
        register(keyCode: keyCode, modifiers: modifiers)
    }

    deinit {
        unregister()
    }

    private func register(keyCode: UInt32, modifiers: UInt32) {
        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )

        let selfPtr = Unmanaged.passUnretained(self).toOpaque()

        let callback: EventHandlerUPP = { _, event, userData in
            guard let event, let userData else { return noErr }
            var hkID = EventHotKeyID()
            let status = GetEventParameter(
                event,
                EventParamName(kEventParamDirectObject),
                EventParamType(typeEventHotKeyID),
                nil,
                MemoryLayout<EventHotKeyID>.size,
                nil,
                &hkID
            )
            guard status == noErr,
                  hkID.signature == HotKey.signature,
                  hkID.id == HotKey.hotKeyID else { return noErr }

            let hotKey = Unmanaged<HotKey>.fromOpaque(userData).takeUnretainedValue()
            Task { @MainActor in
                hotKey.handler()
            }
            return noErr
        }

        InstallEventHandler(
            GetApplicationEventTarget(),
            callback,
            1,
            &eventType,
            selfPtr,
            &eventHandlerRef
        )

        let id = EventHotKeyID(signature: HotKey.signature, id: HotKey.hotKeyID)
        RegisterEventHotKey(
            keyCode,
            modifiers,
            id,
            GetApplicationEventTarget(),
            0,
            &hotKeyRef
        )
    }

    private func unregister() {
        if let hotKeyRef {
            UnregisterEventHotKey(hotKeyRef)
            self.hotKeyRef = nil
        }
        if let eventHandlerRef {
            RemoveEventHandler(eventHandlerRef)
            self.eventHandlerRef = nil
        }
    }
}
