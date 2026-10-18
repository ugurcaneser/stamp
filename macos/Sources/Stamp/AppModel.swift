import Foundation
import StampCore

enum PhaseState: Equatable {
    case pending, active, done, failed
}

enum Outcome {
    case success(JobResult)
    case failure(String)
}

/// UI state for the whole app. Every diskutil/unzip call runs on a background
/// queue and hops back to the main queue to publish, so the window never blocks.
final class AppModel: ObservableObject {
    enum Screen { case select, progress }

    @Published var disks: [Disk] = []
    @Published var selectedID: Disk.ID?
    @Published var scanError: String?

    @Published var zipURL: URL?
    @Published var preview: ZipPreview?
    @Published var zipError: String?
    @Published var isCheckingZip = false

    @Published var label = DiskFormatter.defaultLabel

    @Published var screen: Screen = .select
    @Published var phaseStates: [Phase: PhaseState] = [:]
    @Published var activePhase: Phase?
    @Published var fraction: Double?
    @Published var logLines: [String] = []
    @Published var outcome: Outcome?

    private let queue = DispatchQueue(label: "stamp.work", qos: .userInitiated)
    private var watcher: DiskWatcher?
    private var refreshScheduled = false

    init() {
        watcher = DiskWatcher { [weak self] in self?.scheduleRefresh() }
        watcher?.start()
        refresh()
    }

    var selectedDisk: Disk? {
        disks.first { $0.id == selectedID }
    }

    var sanitizedLabel: String {
        DiskFormatter.sanitizeLabel(label)
    }

    var zipWarnings: [String] {
        preview?.warnings(targetCapacity: selectedDisk?.sizeBytes) ?? []
    }

    var canFormat: Bool {
        selectedDisk != nil && preview != nil && !isCheckingZip
    }

    /// One line next to the Format button saying what's still missing.
    var formatHint: String {
        if selectedDisk == nil { return "Insert a card to continue." }
        if isCheckingZip { return "Checking the zip file…" }
        if preview == nil { return "Choose a zip file to continue." }
        return "Everything on the card will be erased."
    }

    /// The whole job as one 0...1 value: each phase is an equal slice.
    var overallFraction: Double {
        guard let phase = activePhase else { return outcome == nil ? 0 : 1 }
        return (Double(phase.rawValue) + (fraction ?? 0)) / Double(Phase.allCases.count)
    }

    // MARK: Devices

    /// Collapses the burst of DiskArbitration callbacks (one per disk at
    /// startup, several per insertion) into a single rescan.
    private func scheduleRefresh() {
        guard !refreshScheduled else { return }
        refreshScheduled = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.refreshScheduled = false
            self?.refresh()
        }
    }

    func refresh() {
        queue.async { [weak self] in
            let result = Result { Safety.candidates(try DiskScanner().scan()) }
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let disks):
                    self.disks = disks
                    self.scanError = nil
                    if let id = self.selectedID, !disks.contains(where: { $0.id == id }) {
                        self.selectedID = nil
                    }
                    if self.selectedID == nil, let first = disks.first(where: { $0.removable }) {
                        self.selectedID = first.id
                    }
                case .failure(let error):
                    self.scanError = error.localizedDescription
                }
            }
        }
    }

    // MARK: Zip

    func chooseZip(_ url: URL) {
        zipURL = url
        preview = nil
        zipError = nil
        isCheckingZip = true
        StampLog.info("zip selected: \(url.path)")
        queue.async { [weak self] in
            let result = Result { try ContentWriter().preview(zip: url) }
            DispatchQueue.main.async {
                guard let self, self.zipURL == url else { return }
                self.isCheckingZip = false
                switch result {
                case .success(let preview): self.preview = preview
                case .failure(let error): self.zipError = error.localizedDescription
                }
            }
        }
    }

    // MARK: Job

    func start(allowNonRemovable: Bool) {
        guard let disk = selectedDisk, let zip = zipURL else { return }
        StampLog.info("device selected: \(disk.deviceNode) (\(disk.displayModel), \(disk.sizeHuman), removable=\(disk.removable))")
        let request = JobRequest(disk: disk, zip: zip, label: label, allowNonRemovable: allowNonRemovable)
        phaseStates = Dictionary(uniqueKeysWithValues: Phase.allCases.map { ($0, .pending) })
        activePhase = nil
        fraction = nil
        logLines = []
        outcome = nil
        screen = .progress

        queue.async { [weak self] in
            let result = Result {
                try StampJob().run(request) { phase, fraction, message in
                    DispatchQueue.main.async { self?.update(phase: phase, fraction: fraction, message: message) }
                }
            }
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let jobResult):
                    for phase in Phase.allCases { self.phaseStates[phase] = .done }
                    self.outcome = .success(jobResult)
                case .failure(let error):
                    if let phase = self.activePhase { self.phaseStates[phase] = .failed }
                    self.logLines.append("Error: \(error.localizedDescription)")
                    self.outcome = .failure(error.localizedDescription)
                    StampLog.error("job failed: \(error.localizedDescription)")
                }
                self.activePhase = nil
                self.fraction = nil
                self.refresh()
            }
        }
    }

    private func update(phase: Phase, fraction: Double?, message: String?) {
        if activePhase != phase {
            for earlier in Phase.allCases where earlier.rawValue < phase.rawValue {
                phaseStates[earlier] = .done
            }
            phaseStates[phase] = .active
            activePhase = phase
        }
        self.fraction = fraction
        if let message { logLines.append(message) }
    }

    func backToSelection() {
        screen = .select
        outcome = nil
    }
}
