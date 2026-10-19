namespace StampCore;

public enum Phase
{
    CheckingZip,
    Extracting,
    Formatting,
    Mounting,
    Copying,
    Finishing,
}

public static class PhaseTitles
{
    public static string Title(this Phase phase) => phase switch
    {
        Phase.CheckingZip => "Check zip",
        Phase.Extracting => "Extract zip",
        Phase.Formatting => "Format as FAT32",
        Phase.Mounting => "Mount",
        Phase.Copying => "Copy files",
        Phase.Finishing => "Verify and eject",
        _ => phase.ToString(),
    };
}

/// <param name="Disk">The disk as the user saw it when confirming.</param>
public sealed record JobRequest(Disk Disk, string ZipPath, string Label, bool AllowNonRemovable);

public sealed record JobResult(string DiskName, string Label, IReadOnlyList<string> Skipped, bool Ejected);

public sealed class JobException(string message) : Exception(message);

/// <summary>
/// The whole format-and-copy flow, shared by the GUI and the tests. Same order
/// as StampJob.swift: the zip is checked and extracted before the card is
/// touched, so a bad zip never costs a wipe, and the disk is re-scanned right
/// before erasing.
/// </summary>
public sealed class StampJob(DiskScanner scanner, IDiskTools tools, Func<char?> systemDriveLetter)
{
    public delegate void Reporter(Phase phase, double? fraction, string? message);

    public JobResult Run(JobRequest request, Reporter report)
    {
        var label = Fat32Formatter.SanitizeLabel(request.Label);
        var disk = request.Disk;
        StampLog.Info($"job requested: disk={disk.Number} size={disk.SizeBytes} model={disk.DisplayModel} serial={disk.SerialNumber} zip={request.ZipPath} label={label} override={request.AllowNonRemovable}");

        report(Phase.CheckingZip, null, $"Checking {Path.GetFileName(request.ZipPath)}…");
        var preview = ContentWriter.Preview(request.ZipPath);

        var workDir = Path.Combine(Path.GetTempPath(), $"stamp-extract-{Guid.NewGuid():N}");
        try
        {
            report(Phase.Extracting, 0, $"Extracting {preview.FileCount} files…");
            var skipped = ContentWriter.Extract(request.ZipPath, workDir, f => report(Phase.Extracting, f, null));

            // Re-validate against a fresh scan: time has passed since the user
            // confirmed, and the card may have been swapped for another disk.
            report(Phase.Formatting, null, $"Re-checking {disk.DisplayName}…");
            var fresh = scanner.Disk(disk.Number) ?? throw new JobException($"{disk.DisplayName} is no longer attached");
            if (!fresh.SameMedia(disk))
                throw new JobException($"{disk.DisplayName} changed since it was selected (now {fresh.DisplayModel}, {fresh.SizeHuman}); nothing was touched");
            var check = Safety.Evaluate(fresh, systemDriveLetter(), request.AllowNonRemovable);
            if (!check.Ok)
            {
                StampLog.Warning($"refusing to format {fresh.DisplayName}: {string.Join("; ", check.BlockingReasons)}");
                throw new SafetyException(string.Join(" ", check.BlockingReasons));
            }

            report(Phase.Formatting, null, $"Creating a fresh MBR partition table on {fresh.DisplayName}…");
            var letter = tools.Partition(fresh);
            report(Phase.Formatting, 0, $"Formatting {letter}: as FAT32…");
            tools.FormatFat32(letter, label, f => report(Phase.Formatting, f, null));

            report(Phase.Mounting, null, null);
            var root = tools.WaitForFat32(letter);
            report(Phase.Mounting, null, $"{root} is FAT32 (label {label}).");

            report(Phase.Copying, 0, "Copying files…");
            ContentWriter.CopyTree(workDir, root, f => report(Phase.Copying, f, null));

            report(Phase.Finishing, null, "Flushing writes to the card…");
            tools.Flush(letter);
            var missing = ContentWriter.MissingTopLevel(root, preview.TopLevelNames, skipped);
            if (missing.Count > 0)
            {
                StampLog.Error($"verification failed on {root}: missing {string.Join(", ", missing)}");
                throw new JobException($"verification failed — missing after copy: {string.Join(", ", missing)}");
            }

            var ejected = tools.Eject(letter);
            if (!ejected) report(Phase.Finishing, null, $"Warning: could not eject {letter}:. Use \"Safely Remove Hardware\" before removing the card.");
            StampLog.Info($"job complete: {fresh.DisplayName} is FAT32 (label {label}) and loaded");
            return new JobResult(fresh.DisplayName, label, skipped, ejected);
        }
        finally
        {
            try
            {
                if (Directory.Exists(workDir)) Directory.Delete(workDir, recursive: true);
            }
            catch (IOException)
            {
            }
        }
    }
}
