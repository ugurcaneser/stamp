using System.Buffers.Binary;
using System.Text;

namespace StampCore;

public sealed class FormatException(string message) : Exception(message);

/// <summary>
/// Where the FAT32 structures go on a volume. Windows' own format tools
/// refuse FAT32 above 32 GB, so Stamp lays the file system out itself, the
/// same way fat32format and Rufus do (Microsoft's "FAT: General Overview of
/// On-Disk Format" for the BPB and its cluster-size table).
/// </summary>
public sealed record Fat32Layout
{
    public const int ReservedSectors = 32;
    public const int NumberOfFats = 2;
    public const int MinClusters = 65525;  // fewer and it's FAT16 by definition
    public const uint MaxClusters = 0x0FFFFFF5;

    public required int BytesPerSector { get; init; }
    public required long TotalSectors { get; init; }
    public required int SectorsPerCluster { get; init; }
    public required uint FatSectors { get; init; }
    public required uint HiddenSectors { get; init; }
    public required long ClusterCount { get; init; }

    public int BytesPerCluster => BytesPerSector * SectorsPerCluster;
    public long FirstFatSector => ReservedSectors;
    public long FirstDataSector => ReservedSectors + (long)NumberOfFats * FatSectors;

    /// <summary>Microsoft's default FAT32 cluster sizes.</summary>
    public static int ClusterBytesFor(long volumeBytes)
    {
        const long MB = 1024 * 1024, GB = 1024 * MB;
        return volumeBytes switch
        {
            <= 64 * MB => 512,
            <= 128 * MB => 1024,
            <= 256 * MB => 2048,
            <= 8 * GB => 4096,
            <= 16 * GB => 8192,
            <= 32 * GB => 16384,
            _ => 32768,
        };
    }

    public static Fat32Layout Compute(long volumeBytes, int bytesPerSector, long hiddenSectors)
    {
        if (bytesPerSector is not (512 or 1024 or 2048 or 4096))
            throw new FormatException($"unsupported sector size {bytesPerSector}");
        var totalSectors = volumeBytes / bytesPerSector;
        if (totalSectors > uint.MaxValue)
            throw new FormatException($"the volume ({Format.HumanSize(volumeBytes)}) is too large for FAT32");
        if (hiddenSectors is < 0 or > uint.MaxValue)
            throw new FormatException($"partition offset {hiddenSectors} is out of range");

        var sectorsPerCluster = Math.Max(1, ClusterBytesFor(volumeBytes) / bytesPerSector);

        // The FAT needs an entry per cluster plus the two reserved ones:
        //   F * B / 4 >= C + 2,  C = (T - R - N * F) / S
        //   => F >= 4 * (T - R + 2 * S) / (S * B + 4 * N), rounded up.
        // (fat32format's 4 * (T - R) / (S * B + 4 * N) + 1 comes up one entry short at some sizes.)
        var numerator = 4L * (totalSectors - ReservedSectors + 2L * sectorsPerCluster);
        var denominator = (long)sectorsPerCluster * bytesPerSector + 4L * NumberOfFats;
        var fatSectors = (numerator + denominator - 1) / denominator;

        var clusters = (totalSectors - ReservedSectors - NumberOfFats * fatSectors) / sectorsPerCluster;
        if (clusters < MinClusters)
            throw new FormatException($"the volume ({Format.HumanSize(volumeBytes)}) is too small for FAT32");
        if (clusters > MaxClusters)
            throw new FormatException($"the volume ({Format.HumanSize(volumeBytes)}) has too many clusters for FAT32");
        if (fatSectors * bytesPerSector / 4 < clusters + 2)
            throw new FormatException("internal error: FAT too small for the cluster count");

        return new Fat32Layout
        {
            BytesPerSector = bytesPerSector,
            TotalSectors = totalSectors,
            SectorsPerCluster = sectorsPerCluster,
            FatSectors = (uint)fatSectors,
            HiddenSectors = (uint)hiddenSectors,
            ClusterCount = clusters,
        };
    }
}

/// <summary>A volume (or a disk image in tests) that takes sector-aligned writes.</summary>
public interface IBlockDevice
{
    void Write(long offset, ReadOnlySpan<byte> data);
    void Flush();
}

/// <summary>A plain stream, e.g. a disk image file in the tests.</summary>
public sealed class StreamBlockDevice(Stream stream) : IBlockDevice
{
    public void Write(long offset, ReadOnlySpan<byte> data)
    {
        stream.Position = offset;
        stream.Write(data);
    }

    public void Flush() => stream.Flush();
}

public static class Fat32Formatter
{
    public const string DefaultLabel = "STAMP";

    /// <summary>
    /// FAT32 volume labels: up to 11 chars, uppercase, ASCII letters/digits/space/_/-.
    /// Same result as core/formatter.py:sanitize_label and DiskFormatter.sanitizeLabel.
    /// </summary>
    public static string SanitizeLabel(string? label)
    {
        const string allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_ ";
        var cleaned = new string((label ?? "").Trim().Where(c => allowed.Contains(c)).ToArray()).Trim();
        var truncated = cleaned[..Math.Min(cleaned.Length, 11)].Trim();
        return truncated.Length == 0 ? DefaultLabel : truncated.ToUpperInvariant();
    }

    /// <summary>
    /// Writes an empty FAT32 file system: zeroes the reserved area, both FATs and
    /// the root directory cluster, then writes the boot sector (+ backup at 6),
    /// FSInfo (+ backup at 7), the first FAT entries and the volume label entry.
    /// Every write is a whole number of sectors at a sector-aligned offset, as a
    /// raw Windows volume handle requires.
    /// </summary>
    public static void Write(IBlockDevice device, Fat32Layout layout, string label, uint volumeId, Action<double>? progress = null)
    {
        var safeLabel = SanitizeLabel(label);
        var sector = layout.BytesPerSector;

        // 1. Zero everything up to the end of the root directory cluster.
        var zeroSectors = layout.FirstDataSector + layout.SectorsPerCluster;
        var chunkSectors = Math.Max(1, 1024 * 1024 / sector);
        var zeros = new byte[chunkSectors * sector];
        progress?.Invoke(0);
        for (long done = 0; done < zeroSectors;)
        {
            var count = (int)Math.Min(chunkSectors, zeroSectors - done);
            device.Write(done * sector, zeros.AsSpan(0, count * sector));
            done += count;
            progress?.Invoke(0.95 * done / zeroSectors);
        }

        // 2. Boot sector and FSInfo, each with its backup.
        var boot = BootSector(layout, safeLabel, volumeId);
        var fsInfo = FsInfoSector(layout);
        device.Write(0, boot);
        device.Write(1L * sector, fsInfo);
        device.Write(6L * sector, boot);
        device.Write(7L * sector, fsInfo);

        // 3. FAT entries 0 and 1 (media + end-of-chain) and 2 (the root directory's only cluster).
        var fat = new byte[sector];
        BinaryPrimitives.WriteUInt32LittleEndian(fat.AsSpan(0), 0x0FFFFFF8);
        BinaryPrimitives.WriteUInt32LittleEndian(fat.AsSpan(4), 0x0FFFFFFF);
        BinaryPrimitives.WriteUInt32LittleEndian(fat.AsSpan(8), 0x0FFFFFFF);
        for (var i = 0; i < Fat32Layout.NumberOfFats; i++)
            device.Write((layout.FirstFatSector + (long)i * layout.FatSectors) * sector, fat);

        // 4. Root directory: just the volume label entry.
        var root = new byte[sector];
        Encoding.ASCII.GetBytes(safeLabel.PadRight(11)).CopyTo(root, 0);
        root[11] = 0x08;  // ATTR_VOLUME_ID
        device.Write(layout.FirstDataSector * sector, root);

        device.Flush();
        progress?.Invoke(1);
    }

    internal static byte[] BootSector(Fat32Layout layout, string label, uint volumeId)
    {
        var b = new byte[layout.BytesPerSector];
        var s = b.AsSpan();
        b[0] = 0xEB; b[1] = 0x58; b[2] = 0x90;
        Encoding.ASCII.GetBytes("MSWIN4.1").CopyTo(b, 3);
        BinaryPrimitives.WriteUInt16LittleEndian(s[11..], (ushort)layout.BytesPerSector);
        b[13] = (byte)layout.SectorsPerCluster;
        BinaryPrimitives.WriteUInt16LittleEndian(s[14..], Fat32Layout.ReservedSectors);
        b[16] = Fat32Layout.NumberOfFats;
        // 17: RootEntCnt = 0, 19: TotSec16 = 0 (FAT32)
        b[21] = 0xF8;  // media: fixed disk, matching FAT entry 0
        // 22: FATSz16 = 0
        BinaryPrimitives.WriteUInt16LittleEndian(s[24..], 63);   // sectors per track
        BinaryPrimitives.WriteUInt16LittleEndian(s[26..], 255);  // heads
        BinaryPrimitives.WriteUInt32LittleEndian(s[28..], layout.HiddenSectors);
        BinaryPrimitives.WriteUInt32LittleEndian(s[32..], (uint)layout.TotalSectors);
        BinaryPrimitives.WriteUInt32LittleEndian(s[36..], layout.FatSectors);
        // 40: ExtFlags = 0 (FATs mirrored), 42: FSVer = 0
        BinaryPrimitives.WriteUInt32LittleEndian(s[44..], 2);  // root directory cluster
        BinaryPrimitives.WriteUInt16LittleEndian(s[48..], 1);  // FSInfo sector
        BinaryPrimitives.WriteUInt16LittleEndian(s[50..], 6);  // backup boot sector
        b[64] = 0x80;  // drive number
        b[66] = 0x29;  // extended boot signature
        BinaryPrimitives.WriteUInt32LittleEndian(s[67..], volumeId);
        Encoding.ASCII.GetBytes(label.PadRight(11)).CopyTo(b, 71);
        Encoding.ASCII.GetBytes("FAT32   ").CopyTo(b, 82);
        // Not bootable: a tiny stub that just hangs, like other formatters write.
        b[90] = 0xF4; b[91] = 0xEB; b[92] = 0xFD;  // hlt; jmp $-1
        b[510] = 0x55; b[511] = 0xAA;
        return b;
    }

    internal static byte[] FsInfoSector(Fat32Layout layout)
    {
        var b = new byte[layout.BytesPerSector];
        var s = b.AsSpan();
        BinaryPrimitives.WriteUInt32LittleEndian(s[0..], 0x41615252);
        BinaryPrimitives.WriteUInt32LittleEndian(s[484..], 0x61417272);
        BinaryPrimitives.WriteUInt32LittleEndian(s[488..], (uint)(layout.ClusterCount - 1));  // free: all but the root cluster
        BinaryPrimitives.WriteUInt32LittleEndian(s[492..], 3);  // next free cluster hint
        BinaryPrimitives.WriteUInt32LittleEndian(s[508..], 0xAA550000);
        return b;
    }

    /// <summary>A volume serial number derived from the current time, as DOS FORMAT does.</summary>
    public static uint NewVolumeId(DateTime now)
    {
        var lo = (uint)((now.Day + (now.Month << 8)) + ((now.Millisecond / 10 + (now.Second << 8)) & 0xFFFF));
        var hi = (uint)((now.Minute + (now.Hour << 8)) + now.Year);
        return (hi << 16) | (lo & 0xFFFF);
    }
}
