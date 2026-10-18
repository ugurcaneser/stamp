import DiskArbitration
import Foundation

/// Calls `onChange` (on the main run loop) whenever a disk appears,
/// disappears, or is mounted/unmounted. The disk list itself still comes
/// from DiskScanner; DiskArbitration is only the change signal, so there's
/// no polling.
final class DiskWatcher {
    private let onChange: () -> Void
    private var session: DASession?

    init(onChange: @escaping () -> Void) {
        self.onChange = onChange
    }

    func start() {
        guard session == nil, let session = DASessionCreate(kCFAllocatorDefault) else { return }
        self.session = session
        let context = Unmanaged.passUnretained(self).toOpaque()
        DARegisterDiskAppearedCallback(session, nil, { _, context in
            DiskWatcher.fire(context)
        }, context)
        DARegisterDiskDisappearedCallback(session, nil, { _, context in
            DiskWatcher.fire(context)
        }, context)
        DARegisterDiskDescriptionChangedCallback(session, nil, nil, { _, _, context in
            DiskWatcher.fire(context)
        }, context)
        DASessionScheduleWithRunLoop(session, CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue)
    }

    func stop() {
        guard let session else { return }
        DASessionUnscheduleFromRunLoop(session, CFRunLoopGetMain(), CFRunLoopMode.defaultMode.rawValue)
        self.session = nil
    }

    deinit {
        stop()
    }

    private static func fire(_ context: UnsafeMutableRawPointer?) {
        guard let context else { return }
        Unmanaged<DiskWatcher>.fromOpaque(context).takeUnretainedValue().onChange()
    }
}
