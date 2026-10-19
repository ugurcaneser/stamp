namespace StampCore;

public sealed record SafetyCheck(bool Ok, IReadOnlyList<string> BlockingReasons, IReadOnlyList<string> Warnings, bool RequiresOverride);

public sealed class SafetyException(string message) : Exception(message);

/// <summary>
/// Same rules as core/safety.py and Safety.swift: the disk Windows boots or
/// runs from is blocked with no override; non-removable disks need an
/// explicit override; read-only disks and disks too big for MBR are refused.
/// </summary>
public static class Safety
{
    /// <summary>MBR partition entries address at most 2^32 sectors (2 TiB at 512 bytes per sector).</summary>
    public const long MaxMbrBytes = 2L * 1024 * 1024 * 1024 * 1024;

    public static bool BacksRunningSystem(Disk disk, char? systemDriveLetter) =>
        disk.IsBoot || disk.IsSystem
        || (systemDriveLetter is char letter && disk.DriveLetters.Any(l => char.ToUpperInvariant(l) == char.ToUpperInvariant(letter)));

    public static SafetyCheck Evaluate(Disk disk, char? systemDriveLetter, bool allowNonRemovable = false)
    {
        var blocking = new List<string>();
        var warnings = new List<string>();
        var requiresOverride = false;

        if (BacksRunningSystem(disk, systemDriveLetter))
            blocking.Add($"{disk.DisplayName} holds the running Windows installation or its boot partition and cannot be selected.");
        if (disk.IsVirtual)
            blocking.Add($"{disk.DisplayName} is a virtual disk.");
        if (!disk.Removable)
        {
            if (allowNonRemovable)
            {
                warnings.Add($"{disk.DisplayName} is not flagged as removable. Proceeding because you explicitly overrode this check.");
            }
            else
            {
                blocking.Add($"{disk.DisplayName} is not flagged as removable.");
                requiresOverride = true;
            }
        }
        if (disk.IsReadOnly)
            blocking.Add($"{disk.DisplayName} is read-only and cannot be formatted.");
        if (disk.SizeBytes > MaxMbrBytes)
            blocking.Add($"{disk.DisplayName} is larger than 2 TB, which an MBR partition table can't address.");
        if (disk.SizeBytes <= 0)
            blocking.Add($"{disk.DisplayName} reports no media (is the card inserted?).");

        return new SafetyCheck(blocking.Count == 0, blocking, warnings, requiresOverride);
    }

    /// <summary>
    /// Disks fit to even list in the picker. Non-removable disks stay listed so
    /// the user can see them and consciously override.
    /// </summary>
    public static IReadOnlyList<Disk> Candidates(IEnumerable<Disk> disks, char? systemDriveLetter) =>
        disks.Where(d => !BacksRunningSystem(d, systemDriveLetter) && !d.IsVirtual && d.SizeBytes > 0).ToList();

    public static char? SystemDriveLetter()
    {
        var drive = Environment.GetEnvironmentVariable("SystemDrive");
        return string.IsNullOrEmpty(drive) ? null : char.ToUpperInvariant(drive[0]);
    }
}
