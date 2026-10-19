// Runs the real Windows path (diskpart, raw FAT32 writes, Windows mounting
// it, copy, flush, eject) against an attached VHD on a throwaway CI runner:
//   Integration <disk number> <zip>
// Safety refuses virtual, non-removable disks, so for this one disk number
// only, the scan output is rewritten to look like a USB card. The app itself
// has no such switch.
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using StampCore;

[assembly: SupportedOSPlatform("windows")]

var number = int.Parse(args[0]);
var zip = args[1];

var scanner = new DiskScanner(new PretendRemovable(new ProcessRunner(), number));
var disk = scanner.Disk(number) ?? throw new InvalidOperationException($"disk {number} not found");
Console.WriteLine($"target: {disk.DisplayName} {disk.DisplayModel} {disk.SizeHuman}");

var job = new StampJob(scanner, new WindowsDiskTools(new ProcessRunner(), scanner), Safety.SystemDriveLetter);
var result = job.Run(new JobRequest(disk, zip, "ci card", AllowNonRemovable: false), (phase, _, message) =>
{
    if (message is not null) Console.WriteLine($"[{phase.Title()}] {message}");
});
Console.WriteLine($"done: label={result.Label} ejected={result.Ejected} skipped=[{string.Join(", ", result.Skipped)}]");

sealed class PretendRemovable(ICommandRunner inner, int number) : ICommandRunner
{
    public CommandResult Run(string executable, IReadOnlyList<string> arguments, string? stdin = null)
    {
        var result = inner.Run(executable, arguments, stdin);
        if (executable != DiskScanner.PowerShell || result.Status != 0) return result;
        var disks = JsonNode.Parse(result.Stdout)!.AsArray();
        foreach (var disk in disks)
        {
            if (disk!["Number"]!.GetValue<int>() != number) continue;
            disk["MediaType"] = "Removable Media";
            disk["BusType"] = "USB";
        }
        return result with { Stdout = disks.ToJsonString() };
    }
}
