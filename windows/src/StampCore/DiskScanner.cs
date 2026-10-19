using System.Text.Json;

namespace StampCore;

public sealed class DiskScanException(string message) : Exception(message);

/// <summary>
/// Lists physical disks with one PowerShell call: Get-Disk for bus type,
/// boot/system flags and size, Win32_DiskDrive for MediaType (the
/// "Removable Media" flag) and Get-Partition for drive letters.
/// </summary>
public sealed class DiskScanner(ICommandRunner runner)
{
    public DiskScanner() : this(new ProcessRunner()) { }

    public const string PowerShell = "powershell.exe";

    internal const string Script = """
        $ErrorActionPreference = 'Stop'
        $media = @{}
        Get-CimInstance Win32_DiskDrive | ForEach-Object { $media[[int]$_.Index] = [string]$_.MediaType }
        $disks = @(Get-Disk | ForEach-Object {
            $d = $_
            $letters = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue |
                Where-Object { [string]$_.DriveLetter -match '^[A-Za-z]$' } |
                ForEach-Object { [string]$_.DriveLetter })
            [pscustomobject]@{
                Number = [int]$d.Number
                FriendlyName = [string]$d.FriendlyName
                SerialNumber = [string]$d.SerialNumber
                Size = [int64]$d.Size
                BusType = [string]$d.BusType
                MediaType = [string]$media[[int]$d.Number]
                IsBoot = [bool]$d.IsBoot
                IsSystem = [bool]$d.IsSystem
                IsReadOnly = [bool]$d.IsReadOnly
                DriveLetters = $letters
            }
        })
        ConvertTo-Json -InputObject $disks -Depth 3 -Compress
        """;

    public IReadOnlyList<Disk> Scan()
    {
        var result = runner.Run(PowerShell, PowerShellArguments(Script));
        if (result.Status != 0)
            throw new DiskScanException($"could not list disks: {result.Detail}");
        return Parse(result.Stdout);
    }

    /// <summary>-EncodedCommand (UTF-16LE base64) sidesteps command-line quoting of the script entirely.</summary>
    internal static IReadOnlyList<string> PowerShellArguments(string script) =>
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", Convert.ToBase64String(System.Text.Encoding.Unicode.GetBytes(script))];

    public Disk? Disk(int number) => Scan().FirstOrDefault(d => d.Number == number);

    internal static IReadOnlyList<Disk> Parse(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(string.IsNullOrWhiteSpace(json) ? "[]" : json);
            var root = document.RootElement;
            var items = root.ValueKind == JsonValueKind.Array ? root.EnumerateArray().ToList() : [root];
            return items.Select(ToDisk).OrderBy(d => d.Number).ToList();
        }
        catch (JsonException exc)
        {
            throw new DiskScanException($"could not parse the disk list: {exc.Message}");
        }
    }

    static Disk ToDisk(JsonElement e) => new()
    {
        Number = e.GetProperty("Number").GetInt32(),
        SizeBytes = e.TryGetProperty("Size", out var size) && size.ValueKind == JsonValueKind.Number ? size.GetInt64() : 0,
        FriendlyName = Text(e, "FriendlyName"),
        SerialNumber = Text(e, "SerialNumber"),
        BusType = Text(e, "BusType"),
        Removable = string.Equals(Text(e, "MediaType"), "Removable Media", StringComparison.OrdinalIgnoreCase),
        IsBoot = Flag(e, "IsBoot"),
        IsSystem = Flag(e, "IsSystem"),
        IsReadOnly = Flag(e, "IsReadOnly"),
        DriveLetters = e.TryGetProperty("DriveLetters", out var letters) ? Letters(letters) : [],
    };

    static string? Text(JsonElement e, string name) =>
        e.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String && value.GetString()!.Trim().Length > 0
            ? value.GetString()!.Trim()
            : null;

    static bool Flag(JsonElement e, string name) =>
        e.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;

    static IReadOnlyList<char> Letters(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Array => value.EnumerateArray()
            .Where(v => v.ValueKind == JsonValueKind.String && v.GetString()!.Length == 1)
            .Select(v => char.ToUpperInvariant(v.GetString()![0])).ToList(),
        JsonValueKind.String when value.GetString()!.Length == 1 => [char.ToUpperInvariant(value.GetString()![0])],
        _ => [],
    };
}
