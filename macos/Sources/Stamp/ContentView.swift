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

struct SelectView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showingImporter = false
    @State private var showingConfirm = false

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    if model.disks.isEmpty {
                        ContentUnavailableView(
                            "No drives found",
                            systemImage: "sdcard",
                            description: Text("Insert an SD card or USB drive.")
                        )
                    } else {
                        List(model.disks, selection: $model.selectedID) { disk in
                            DiskRow(disk: disk).tag(disk.id)
                        }
                        .frame(minHeight: 140)
                    }
                    if let error = model.scanError {
                        Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                    }
                } header: {
                    HStack {
                        Text("Device")
                        Spacer()
                        Button("Refresh", systemImage: "arrow.clockwise") { model.refresh() }
                            .labelStyle(.iconOnly)
                            .buttonStyle(.borderless)
                    }
                }

                Section("Zip contents") {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(model.zipURL?.lastPathComponent ?? "No zip file selected")
                            Text(zipSubtitle).font(.callout).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if model.isCheckingZip { ProgressView().controlSize(.small) }
                        Button("Choose Zip File…") { showingImporter = true }
                    }
                    if let error = model.zipError {
                        Label(error, systemImage: "xmark.octagon").foregroundStyle(.red)
                    }
                    ForEach(model.zipWarnings, id: \.self) { warning in
                        Label(warning, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                    }
                }

                Section {
                    TextField("FAT32 label (max 11 characters)", text: $model.label)
                } header: {
                    Text("Volume label")
                } footer: {
                    Text("Written as \(model.sanitizedLabel)").foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)

            Button(role: .destructive) {
                showingConfirm = true
            } label: {
                Text("Format & Copy…").frame(minWidth: 160)
            }
            .controlSize(.large)
            .disabled(!model.canFormat)
            .padding(.bottom, 20)
        }
        .fileImporter(isPresented: $showingImporter, allowedContentTypes: [.zip]) { result in
            if case .success(let url) = result { model.chooseZip(url) }
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

    private var zipSubtitle: String {
        guard let preview = model.preview else { return "Choose a .zip to copy onto the card" }
        return "\(preview.fileCount) files · \(humanSize(preview.uncompressedBytes))"
    }
}

struct DiskRow: View {
    let disk: Disk

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: disk.removable ? "sdcard" : "externaldrive")
                .font(.title2)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(disk.displayModel)
                Text(details).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if !disk.removable {
                Label("Not removable", systemImage: "exclamationmark.triangle")
                    .font(.callout)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 2)
    }

    private var details: String {
        var parts = [disk.deviceNode, disk.sizeHuman]
        if let bus = disk.busProtocol { parts.append(bus) }
        if !disk.mountPoints.isEmpty { parts.append(disk.mountPoints.joined(separator: ", ")) }
        return parts.joined(separator: " · ")
    }
}

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
        VStack(alignment: .leading, spacing: 14) {
            Text("Format \(disk.deviceNode)?").font(.title2.bold())
            VStack(alignment: .leading, spacing: 4) {
                Text("\(disk.deviceNode)  ·  \(disk.sizeHuman)  ·  \(disk.displayModel)")
                Text("\(zip.lastPathComponent)  ·  \(preview.fileCount) files  ·  \(humanSize(preview.uncompressedBytes))")
                Text("FAT32 label: \(label)")
            }
            .foregroundStyle(.secondary)
            Text("Everything on this device will be erased.")
            if !disk.removable {
                Toggle("Use this device even though it is not marked removable", isOn: $acceptNonRemovable)
                    .toggleStyle(.checkbox)
            }
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Format & Copy", role: .destructive) { onConfirm(!disk.removable && acceptNonRemovable) }
                    .disabled(!disk.removable && !acceptNonRemovable)
            }
        }
        .padding(24)
        .frame(width: 460)
    }
}

struct ProgressScreen: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ForEach(Phase.allCases, id: \.self) { phase in
                HStack(spacing: 10) {
                    icon(for: model.phaseStates[phase] ?? .pending).frame(width: 20)
                    Text(phase.title)
                    Spacer()
                    if model.activePhase == phase {
                        if let fraction = model.fraction {
                            ProgressView(value: fraction).frame(width: 180)
                        } else {
                            ProgressView().controlSize(.small)
                        }
                    }
                }
            }

            ScrollView {
                Text(model.logLines.joined(separator: "\n"))
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
            }
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))

            if let outcome = model.outcome {
                outcomeView(outcome)
            }
        }
        .padding(24)
    }

    @ViewBuilder
    private func icon(for state: PhaseState) -> some View {
        switch state {
        case .pending: Image(systemName: "circle").foregroundStyle(.tertiary)
        case .active: Image(systemName: "circle.dotted").foregroundStyle(.blue)
        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
        }
    }

    @ViewBuilder
    private func outcomeView(_ outcome: Outcome) -> some View {
        HStack(alignment: .top) {
            switch outcome {
            case .success(let result):
                VStack(alignment: .leading, spacing: 4) {
                    Label("\(result.deviceNode) is formatted FAT32 (label \(result.label)) and loaded.", systemImage: "checkmark.seal.fill")
                        .foregroundStyle(.green)
                    Text(result.ejected ? "It has been ejected and is safe to remove." : "Eject it from Finder before removing it.")
                    if !result.skipped.isEmpty {
                        Text("Skipped symlinks (FAT32 can't store them): \(result.skipped.joined(separator: ", "))")
                            .foregroundStyle(.orange)
                    }
                }
                Spacer()
                Button("Prepare Another") { model.backToSelection() }
            case .failure(let message):
                Label(message, systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                Spacer()
                Button("Back") { model.backToSelection() }
            }
        }
    }
}
