import AppKit
import StampCore
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        switch model.screen {
        case .select: SelectView()
        case .progress: ProgressScreen()
        }
    }
}

// MARK: - Select

struct SelectView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showingConfirm = false

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(spacing: 16) {
                    AppHeader()
                    StepCard(number: 1, title: "Choose a card") {
                        DiskPicker()
                    }
                    StepCard(number: 2, title: "Choose a zip file") {
                        ZipDropZone()
                    }
                    StepCard(number: 3, title: "Name the card") {
                        LabelField()
                    }
                }
                .padding(24)
            }

            Divider()

            HStack(spacing: 16) {
                Text(model.formatHint)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    showingConfirm = true
                } label: {
                    Text("Format & Copy…").frame(minWidth: 140)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
                .disabled(!model.canFormat)
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
        }
        .sheet(isPresented: $showingConfirm) {
            if let disk = model.selectedDisk, let zip = model.zipURL, let preview = model.preview {
                ConfirmSheet(disk: disk, zip: zip, preview: preview, label: model.sanitizedLabel) { allowNonRemovable in
                    showingConfirm = false
                    model.start(allowNonRemovable: allowNonRemovable)
                }
            }
        }
    }
}

struct AppHeader: View {
    var body: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
                .frame(width: 56, height: 56)
            VStack(alignment: .leading, spacing: 2) {
                Text("Stamp").font(.title.bold())
                Text("Erase a card as FAT32 and copy a zip's contents onto it.")
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(.bottom, 4)
    }
}

struct StepCard<Content: View>: View {
    let number: Int
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Text("\(number)")
                    .font(.callout.bold())
                    .foregroundStyle(.white)
                    .frame(width: 22, height: 22)
                    .background(Circle().fill(Color.accentColor))
                Text(title).font(.headline)
            }
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.separator))
    }
}

// MARK: Step 1

struct DiskPicker: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 8) {
            if model.disks.isEmpty {
                HStack(spacing: 12) {
                    Image(systemName: "sdcard")
                        .font(.title)
                        .foregroundStyle(.tertiary)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("No card found")
                        Text("Insert an SD card or USB drive. It shows up here automatically.")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(12)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            } else {
                ForEach(model.disks) { disk in
                    DiskRow(disk: disk, selected: disk.id == model.selectedID) {
                        model.selectedID = disk.id
                    }
                }
            }
            if let error = model.scanError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.callout)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct DiskRow: View {
    let disk: Disk
    let selected: Bool
    let onSelect: () -> Void

    var body: some View {
        Button(action: onSelect) {
            HStack(spacing: 12) {
                Image(systemName: disk.removable ? "sdcard.fill" : "externaldrive.fill")
                    .font(.title2)
                    .foregroundStyle(selected ? Color.accentColor : .secondary)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(disk.displayModel) · \(disk.sizeHuman)")
                    Text(details).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(selected ? Color.accentColor : Color.secondary.opacity(0.5))
            }
            .padding(12)
            .contentShape(Rectangle())
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(selected ? Color.accentColor.opacity(0.1) : Color.secondary.opacity(0.06))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(selected ? Color.accentColor : .clear, lineWidth: 1.5)
            )
        }
        .buttonStyle(.plain)
    }

    private var details: String {
        var parts = [disk.deviceNode]
        if let bus = disk.busProtocol { parts.append(bus) }
        if !disk.removable { parts.append("not marked removable") }
        return parts.joined(separator: " · ")
    }
}

// MARK: Step 2

struct ZipDropZone: View {
    @EnvironmentObject private var model: AppModel
    @State private var showingImporter = false
    @State private var isTargeted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Group {
                if let url = model.zipURL {
                    selected(url)
                } else {
                    empty
                }
            }
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(isTargeted ? Color.accentColor.opacity(0.12) : Color.secondary.opacity(0.06))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(
                        isTargeted ? Color.accentColor : Color.secondary.opacity(0.4),
                        style: StrokeStyle(lineWidth: 1.5, dash: model.zipURL == nil ? [6, 4] : [])
                    )
            )
            .dropDestination(for: URL.self) { urls, _ in
                guard let url = urls.first, url.pathExtension.lowercased() == "zip" else { return false }
                model.chooseZip(url)
                return true
            } isTargeted: { isTargeted = $0 }

            if let error = model.zipError {
                Label(error, systemImage: "xmark.octagon").font(.callout).foregroundStyle(.red)
            }
            ForEach(model.zipWarnings, id: \.self) { warning in
                Label(warning, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
            }
        }
        .fileImporter(isPresented: $showingImporter, allowedContentTypes: [.zip]) { result in
            if case .success(let url) = result { model.chooseZip(url) }
        }
    }

    private var empty: some View {
        VStack(spacing: 8) {
            Image(systemName: "doc.zipper")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("Drop a zip file here")
            Button("Choose File…") { showingImporter = true }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
    }

    private func selected(_ url: URL) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "doc.zipper")
                .font(.title2)
                .foregroundStyle(Color.accentColor)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(url.lastPathComponent).lineLimit(1).truncationMode(.middle)
                Text(subtitle).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if model.isCheckingZip { ProgressView().controlSize(.small) }
            Button("Change…") { showingImporter = true }
        }
        .padding(12)
    }

    private var subtitle: String {
        if model.isCheckingZip { return "Checking…" }
        guard let preview = model.preview else { return "Can't be used" }
        return "\(preview.fileCount) files · \(humanSize(preview.uncompressedBytes))"
    }
}

// MARK: Step 3

struct LabelField: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            TextField("STAMP", text: $model.label)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 240)
            Group {
                if model.sanitizedLabel != model.label {
                    Text("Will be written as \(model.sanitizedLabel)")
                } else {
                    Text("Shown as the card's name. Up to 11 letters or digits.")
                }
            }
            .font(.callout)
            .foregroundStyle(.secondary)
        }
    }
}

// MARK: - Confirm

/// The single "Format & Copy" gate, same shape as gui/confirm_dialog.py: one
/// summary, plus one risk checkbox only for disks not flagged removable.
struct ConfirmSheet: View {
    let disk: Disk
    let zip: URL
    let preview: ZipPreview
    let label: String
    let onConfirm: (_ allowNonRemovable: Bool) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var acceptNonRemovable = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.largeTitle)
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Erase \(disk.displayModel)?").font(.title2.bold())
                    Text("Everything on this card will be deleted.").foregroundStyle(.secondary)
                }
            }
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                GridRow {
                    Text("Card").foregroundStyle(.secondary)
                    Text("\(disk.displayModel) · \(disk.sizeHuman) · \(disk.deviceNode)")
                }
                GridRow {
                    Text("Zip").foregroundStyle(.secondary)
                    Text("\(zip.lastPathComponent) · \(preview.fileCount) files · \(humanSize(preview.uncompressedBytes))")
                }
                GridRow {
                    Text("Name").foregroundStyle(.secondary)
                    Text(label)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))

            if !disk.removable {
                Toggle("Use this device even though it is not marked removable", isOn: $acceptNonRemovable)
                    .toggleStyle(.checkbox)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Erase & Copy", role: .destructive) { onConfirm(!disk.removable && acceptNonRemovable) }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .disabled(!disk.removable && !acceptNonRemovable)
            }
        }
        .padding(24)
        .frame(width: 480)
    }
}

// MARK: - Progress

struct ProgressScreen: View {
    @EnvironmentObject private var model: AppModel
    @State private var showingDetails = false

    var body: some View {
        VStack(spacing: 20) {
            Spacer(minLength: 0)
            switch model.outcome {
            case nil: running
            case .success(let result): success(result)
            case .failure(let message): failure(message)
            }

            DisclosureGroup("Details", isExpanded: $showingDetails) {
                ScrollView {
                    Text(model.logLines.joined(separator: "\n"))
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                }
                .frame(height: 140)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
            }
            Spacer(minLength: 0)
        }
        .padding(32)
        .frame(maxWidth: 520)
        .frame(maxWidth: .infinity)
    }

    private var running: some View {
        VStack(spacing: 20) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
                .frame(width: 80, height: 80)
            VStack(spacing: 4) {
                Text("Preparing your card…").font(.title2.bold())
                Text(model.activePhase?.title ?? "Starting")
                    .foregroundStyle(.secondary)
            }
            ProgressView(value: model.overallFraction)
            Text("Don't remove the card until Stamp is done.")
                .font(.callout)
                .foregroundStyle(.secondary)
            PhaseList()
        }
    }

    private func success(_ result: JobResult) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 64))
                .foregroundStyle(.green)
            VStack(spacing: 4) {
                Text("Your card is ready").font(.title2.bold())
                Text(result.ejected
                    ? "\(result.label) was ejected and is safe to remove."
                    : "Eject \(result.label) from Finder before removing it.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            if !result.skipped.isEmpty {
                Label("Skipped symlinks (FAT32 can't store them): \(result.skipped.joined(separator: ", "))",
                      systemImage: "exclamationmark.triangle")
                    .font(.callout)
                    .foregroundStyle(.orange)
            }
            Button("Prepare Another Card") { model.backToSelection() }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
        }
    }

    private func failure(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "xmark.octagon.fill")
                .font(.system(size: 64))
                .foregroundStyle(.red)
            VStack(spacing: 4) {
                Text("Something went wrong").font(.title2.bold())
                Text(message)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .textSelection(.enabled)
            }
            PhaseList()
            Button("Back") { model.backToSelection() }
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
        }
    }
}

struct PhaseList: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Phase.allCases, id: \.self) { phase in
                HStack(spacing: 8) {
                    icon(for: model.phaseStates[phase] ?? .pending).frame(width: 18)
                    Text(phase.title)
                        .foregroundStyle(model.phaseStates[phase] == .pending ? .secondary : .primary)
                }
                .font(.callout)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private func icon(for state: PhaseState) -> some View {
        switch state {
        case .pending: Image(systemName: "circle").foregroundStyle(.tertiary)
        case .active: ProgressView().controlSize(.mini)
        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        }
    }
}
