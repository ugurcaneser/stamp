namespace StampCore;

/// <summary>A physical disk as reported by Get-Disk plus Win32_DiskDrive.</summary>
public sealed record Disk
{
    public required int Number { get; init; }
    public required long SizeBytes { get; init; }
    public string? FriendlyName { get; init; }
    public string? SerialNumber { get; init; }
    /// <summary>Get-Disk BusType: "USB", "SD", "MMC", "SATA", "NVMe", "File Backed Virtual", ...</summary>
    public string? BusType { get; init; }
    /// <summary>Win32_DiskDrive MediaType "Removable Media", the Windows analogue of lsblk RM=1.</summary>
    public bool Removable { get; init; }
    public bool IsBoot { get; init; }
    public bool IsSystem { get; init; }
    public bool IsReadOnly { get; init; }
    /// <summary>Drive letters of the disk's partitions, e.g. ['E'].</summary>
    public IReadOnlyList<char> DriveLetters { get; init; } = [];

    public string DevicePath => $@"\\.\PhysicalDrive{Number}";
    public string DisplayName => $"Disk {Number}";
    public string SizeHuman => Format.HumanSize(SizeBytes);

    public string DisplayModel =>
        string.IsNullOrWhiteSpace(FriendlyName) ? "Unknown device" : FriendlyName.Trim();

    public bool IsVirtual =>
        BusType is not null && (BusType.Contains("Virtual", StringComparison.OrdinalIgnoreCase)
                                || BusType.Equals("File Backed Virtual", StringComparison.OrdinalIgnoreCase));

    public string Details
    {
        get
        {
            var parts = new List<string> { DisplayName };
            if (!string.IsNullOrEmpty(BusType)) parts.Add(BusType);
            if (DriveLetters.Count > 0) parts.Add(string.Join(", ", DriveLetters.Select(l => $"{l}:")));
            return string.Join(" · ", parts);
        }
    }

    /// <summary>Equality used for "the disk changed since it was confirmed" (records also compare lists by reference).</summary>
    public bool SameMedia(Disk other) =>
        Number == other.Number && SizeBytes == other.SizeBytes && DisplayModel == other.DisplayModel
        && (SerialNumber ?? "").Trim() == (other.SerialNumber ?? "").Trim();
}

public static class Format
{
    /// <summary>Binary units with GB/TB-style labels, matching core/models.py:human_size.</summary>
    public static string HumanSize(long bytes)
    {
        double value = bytes;
        string[] units = ["B", "KB", "MB", "GB", "TB", "PB"];
        foreach (var unit in units)
        {
            if (value < 1024.0 || unit == "PB")
            {
                return unit == "B"
                    ? $"{(long)value} B"
                    : string.Create(System.Globalization.CultureInfo.InvariantCulture, $"{value:0.0} {unit}");
            }
            value /= 1024.0;
        }
        return string.Create(System.Globalization.CultureInfo.InvariantCulture, $"{value:0.0} PB");
    }
}
