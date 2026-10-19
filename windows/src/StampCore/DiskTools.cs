using System.Buffers.Binary;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Win32.SafeHandles;

namespace StampCore;

/// <summary>The disk-touching steps of the job, behind one seam so StampJob can be tested with a fake.</summary>
public interface IDiskTools
{
    /// <summary>Wipes the partition table and creates one MBR partition of type 0x0C (FAT32 LBA). Returns its drive letter.</summary>
    char Partition(Disk disk);

    /// <summary>Lays a FAT32 file system onto the (still RAW) partition at <paramref name="letter"/>.</summary>
    void FormatFat32(char letter, string label, Action<double> progress);

    /// <summary>Waits for Windows to mount the new FAT32 volume and returns its root, e.g. "E:\".</summary>
    string WaitForFat32(char letter);

    void Flush(char letter);

    /// <summary>Flushes, dismounts and ejects the volume. Returns false (and logs) if Windows refused.</summary>
    bool Eject(char letter);
}

/// <summary>
/// diskpart for the partition table, then raw volume writes for FAT32, which
/// Windows' own format tools refuse above 32 GB. Needs administrator rights;
/// the app's manifest asks for them at launch.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowsDiskTools(ICommandRunner runner, DiskScanner scanner) : IDiskTools
{
    public WindowsDiskTools() : this(new ProcessRunner(), new DiskScanner()) { }

    static string DiskPartExe => Path.Combine(Environment.SystemDirectory, "diskpart.exe");

    /// <summary>"clean" also dismounts the old volumes; align=1024 starts the partition at 1 MiB.</summary>
    internal static string PartitionScript(int diskNumber) => string.Join("\r\n",
        $"select disk {diskNumber}",
        "clean",
        "convert mbr",
        "create partition primary align=1024 id=0c",
        "assign",
        "exit") + "\r\n";

    public char Partition(Disk disk)
    {
        var script = Path.Combine(Path.GetTempPath(), $"stamp-diskpart-{Guid.NewGuid():N}.txt");
        File.WriteAllText(script, PartitionScript(disk.Number));
        try
        {
            StampLog.Info($"diskpart script for disk {disk.Number}:\n{PartitionScript(disk.Number)}");
            var result = runner.Run(DiskPartExe, ["/s", script]);
            if (result.Status != 0)
                throw new FormatException($"diskpart failed on {disk.DisplayName}: {LastLines(result.Stdout)}");
        }
        finally
        {
            File.Delete(script);
        }

        // The new volume gets its letter asynchronously.
        for (var attempt = 0; attempt < 40; attempt++)
        {
            var fresh = scanner.Disk(disk.Number);
            if (fresh is { DriveLetters.Count: > 0 }) return fresh.DriveLetters[0];
            Thread.Sleep(500);
        }
        throw new FormatException($"the new partition on {disk.DisplayName} never got a drive letter");
    }

    public void FormatFat32(char letter, string label, Action<double> progress)
    {
        using var volume = Volume.Open(letter);
        volume.AllowExtendedIo();
        volume.Lock();
        var (bytesPerSector, offset, length) = volume.Geometry();
        var layout = Fat32Layout.Compute(length, bytesPerSector, offset / bytesPerSector);
        StampLog.Info($"FAT32 layout for {letter}: {layout}");
        Fat32Formatter.Write(volume, layout, label, Fat32Formatter.NewVolumeId(DateTime.Now), progress);
        volume.Dismount();  // Windows re-mounts it, now recognised as FAT32
    }

    public string WaitForFat32(char letter)
    {
        var root = $"{letter}:\\";
        string? seen = null;
        for (var attempt = 0; attempt < 60; attempt++)
        {
            try
            {
                var drive = new DriveInfo(root);
                if (drive.IsReady)
                {
                    seen = drive.DriveFormat;
                    if (seen == "FAT32") return root;
                }
            }
            catch (IOException)
            {
            }
            Thread.Sleep(500);
        }
        throw new FormatException(seen is null
            ? $"{root} did not come back after formatting"
            : $"{root} mounted as {seen}, not FAT32");
    }

    public void Flush(char letter)
    {
        using var volume = Volume.Open(letter);
        volume.Flush();
    }

    public bool Eject(char letter)
    {
        try
        {
            using var volume = Volume.Open(letter);
            volume.Flush();
            volume.Lock();
            volume.Dismount();
            volume.AllowRemoval();
            volume.EjectMedia();
            return true;
        }
        catch (Win32Exception exc)
        {
            StampLog.Warning($"could not eject {letter}: {exc.Message}");
            return false;
        }
    }

    static string LastLines(string output) =>
        string.Join(" ", output.Split('\n').Select(l => l.Trim()).Where(l => l.Length > 0).TakeLast(3));

    /// <summary>A raw \\.\X: volume handle and the handful of IOCTLs Stamp needs.</summary>
    sealed class Volume : IBlockDevice, IDisposable
    {
        const uint GenericRead = 0x80000000, GenericWrite = 0x40000000;
        const uint FileShareRead = 1, FileShareWrite = 2, OpenExisting = 3, FileFlagWriteThrough = 0x80000000;
        const uint IoctlDiskGetDriveGeometry = 0x00070000;
        const uint IoctlDiskGetPartitionInfoEx = 0x00070048;
        const uint FsctlLockVolume = 0x00090018;
        const uint FsctlDismountVolume = 0x00090020;
        const uint FsctlAllowExtendedDasdIo = 0x00090083;
        const uint IoctlStorageMediaRemoval = 0x002D4804;
        const uint IoctlStorageEjectMedia = 0x002D4808;

        readonly SafeFileHandle handle;
        readonly char letter;

        Volume(SafeFileHandle handle, char letter)
        {
            this.handle = handle;
            this.letter = letter;
        }

        public static Volume Open(char letter)
        {
            if (!char.IsAsciiLetter(letter)) throw new ArgumentException($"not a drive letter: {letter}");
            var handle = CreateFileW($@"\\.\{char.ToUpperInvariant(letter)}:", GenericRead | GenericWrite,
                FileShareRead | FileShareWrite, IntPtr.Zero, OpenExisting, FileFlagWriteThrough, IntPtr.Zero);
            if (handle.IsInvalid) throw Error($"open {letter}:");
            return new Volume(handle, letter);
        }

        public void AllowExtendedIo() => Control(FsctlAllowExtendedDasdIo, [], [], "allow extended I/O on");
        public void Dismount() => Control(FsctlDismountVolume, [], [], "dismount");
        public void AllowRemoval() => Control(IoctlStorageMediaRemoval, [0], [], "allow removal of");
        public void EjectMedia() => Control(IoctlStorageEjectMedia, [], [], "eject");

        /// <summary>Lock fails while something (Explorer, antivirus) still has the volume open; retry briefly.</summary>
        public void Lock()
        {
            for (var attempt = 0; ; attempt++)
            {
                try
                {
                    Control(FsctlLockVolume, [], [], "lock");
                    return;
                }
                catch (Win32Exception) when (attempt < 20)
                {
                    Thread.Sleep(250);
                }
            }
        }

        public (int BytesPerSector, long Offset, long Length) Geometry()
        {
            var geometry = new byte[24];
            Control(IoctlDiskGetDriveGeometry, [], geometry, "read the geometry of");
            var partition = new byte[144];
            Control(IoctlDiskGetPartitionInfoEx, [], partition, "read the partition of");
            return ((int)BinaryPrimitives.ReadUInt32LittleEndian(geometry.AsSpan(20)),
                BinaryPrimitives.ReadInt64LittleEndian(partition.AsSpan(8)),
                BinaryPrimitives.ReadInt64LittleEndian(partition.AsSpan(16)));
        }

        public unsafe void Write(long offset, ReadOnlySpan<byte> data)
        {
            if (!SetFilePointerEx(handle, offset, out _, 0)) throw Error($"seek on {letter}:");
            fixed (byte* p = data)
            {
                if (!WriteFile(handle, p, (uint)data.Length, out var written, IntPtr.Zero) || written != data.Length)
                    throw Error($"write to {letter}: at offset {offset}");
            }
        }

        public void Flush()
        {
            if (!FlushFileBuffers(handle)) throw Error($"flush {letter}:");
        }

        public void Dispose() => handle.Dispose();

        unsafe void Control(uint code, byte[] input, byte[] output, string what)
        {
            fixed (byte* inp = input)
            fixed (byte* outp = output)
            {
                if (!DeviceIoControl(handle, code, inp, (uint)input.Length, outp, (uint)output.Length, out _, IntPtr.Zero))
                    throw Error($"{what} {letter}:");
            }
        }

        static Win32Exception Error(string what)
        {
            var code = Marshal.GetLastPInvokeError();
            return new Win32Exception(code, $"could not {what}: {new Win32Exception(code).Message}");
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern unsafe bool DeviceIoControl(SafeFileHandle device, uint code, byte* input, uint inputSize, byte* output, uint outputSize, out uint returned, IntPtr overlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool SetFilePointerEx(SafeFileHandle file, long distance, out long newPointer, uint method);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern unsafe bool WriteFile(SafeFileHandle file, byte* buffer, uint count, out uint written, IntPtr overlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool FlushFileBuffers(SafeFileHandle file);
    }
}
