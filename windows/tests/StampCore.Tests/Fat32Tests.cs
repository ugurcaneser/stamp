using System.Buffers.Binary;
using System.Diagnostics;
using StampCore;
using Xunit;

namespace StampCore.Tests;

public class Fat32Tests
{
    const long MB = 1024 * 1024, GB = 1024 * MB;

    [Theory]
    [InlineData(64 * MB, 1)]
    [InlineData(4 * GB, 8)]
    [InlineData(16 * GB, 16)]
    [InlineData(32 * GB, 32)]
    [InlineData(64 * GB, 64)]
    [InlineData(128 * GB, 64)]
    public void UsesMicrosoftClusterSizes(long bytes, int sectorsPerCluster)
    {
        var layout = Fat32Layout.Compute(bytes, 512, 2048);
        Assert.Equal(sectorsPerCluster, layout.SectorsPerCluster);
    }

    [Theory]
    [InlineData(40 * MB)]
    [InlineData(4 * GB + 12345 * 512L)]
    [InlineData(59 * GB + 7 * MB)]
    [InlineData(500 * GB)]
    public void FatCoversEveryCluster(long bytes)
    {
        var layout = Fat32Layout.Compute(bytes, 512, 2048);
        Assert.True(layout.ClusterCount >= Fat32Layout.MinClusters);
        Assert.True((long)layout.FatSectors * 512 / 4 >= layout.ClusterCount + 2);
        // The data region holds every cluster the FAT describes.
        Assert.True(layout.FirstDataSector + layout.ClusterCount * layout.SectorsPerCluster <= layout.TotalSectors);
    }

    [Fact]
    public void RefusesVolumesTooSmallForFat32()
    {
        var error = Assert.Throws<StampCore.FormatException>(() => Fat32Layout.Compute(30 * MB, 512, 2048));
        Assert.Contains("too small", error.Message);
    }

    [Fact]
    public void RefusesVolumesBeyondTwoTerabytes()
    {
        Assert.Throws<StampCore.FormatException>(() => Fat32Layout.Compute(3L * 1024 * GB, 512, 2048));
    }

    [Fact]
    public void BootSectorFields()
    {
        var layout = Fat32Layout.Compute(64 * GB, 512, 2048);
        var boot = Fat32Formatter.BootSector(layout, "CARD", 0x12345678);
        Assert.Equal(512, BinaryPrimitives.ReadUInt16LittleEndian(boot.AsSpan(11)));
        Assert.Equal(64, boot[13]);
        Assert.Equal(2048u, BinaryPrimitives.ReadUInt32LittleEndian(boot.AsSpan(28)));
        Assert.Equal(layout.FatSectors, BinaryPrimitives.ReadUInt32LittleEndian(boot.AsSpan(36)));
        Assert.Equal("CARD       ", System.Text.Encoding.ASCII.GetString(boot, 71, 11));
        Assert.Equal("FAT32   ", System.Text.Encoding.ASCII.GetString(boot, 82, 8));
        Assert.Equal(0x55, boot[510]);
        Assert.Equal(0xAA, boot[511]);
    }

    [Theory]
    [InlineData("my card", "MY CARD")]
    [InlineData("  ", "STAMP")]
    [InlineData(null, "STAMP")]
    [InlineData("ÇİĞ:card/2024", "CARD2024")]
    [InlineData("averyveryverylonglabel", "AVERYVERYVE")]
    public void SanitizesLabelLikeTheOtherPlatforms(string? input, string expected)
    {
        Assert.Equal(expected, Fat32Formatter.SanitizeLabel(input));
    }

    // ----- real images, checked with dosfstools/mtools when they're installed -----

    [Theory]
    [InlineData(48 * MB)]
    [InlineData(40 * GB)]  // > 32 GB: what Windows' own tools refuse to make
    public void ImagePassesFsckAndTakesFiles(long bytes)
    {
        // CI sets STAMP_REQUIRE_FAT_TOOLS=1 on Linux so these can't silently skip there.
        if (!Tools.Have("fsck.fat") || !Tools.Have("mcopy"))
        {
            Assert.True(Environment.GetEnvironmentVariable("STAMP_REQUIRE_FAT_TOOLS") != "1", "fsck.fat/mtools not found");
            return;
        }

        var image = Path.Combine(Path.GetTempPath(), $"stamp-fat32-{Guid.NewGuid():N}.img");
        try
        {
            using (var stream = new FileStream(image, FileMode.CreateNew))
            {
                stream.SetLength(bytes);  // sparse where the file system supports it
                var layout = Fat32Layout.Compute(bytes, 512, 0);
                Fat32Formatter.Write(new StreamBlockDevice(stream), layout, "test card", 0xCAFEF00D);
            }

            Tools.Run("fsck.fat", "-n", "-v", image).AssertOk("fsck.fat on the fresh image");

            var label = Tools.Run("mlabel", "-i", image, "-s", "::");
            label.AssertOk("mlabel");
            Assert.Contains("TEST CARD", label.Output);

            var payload = Path.Combine(Path.GetTempPath(), $"stamp-payload-{Guid.NewGuid():N}.txt");
            File.WriteAllText(payload, "hello from stamp");
            try
            {
                Tools.Run("mmd", "-i", image, "::/firmware").AssertOk("mmd");
                Tools.Run("mcopy", "-i", image, payload, "::/firmware/readme.txt").AssertOk("mcopy");
            }
            finally
            {
                File.Delete(payload);
            }
            Tools.Run("fsck.fat", "-n", image).AssertOk("fsck.fat after writing files");
            var read = Tools.Run("mtype", "-i", image, "::/firmware/readme.txt");
            read.AssertOk("mtype");
            Assert.Equal("hello from stamp", read.Output.Trim());
        }
        finally
        {
            File.Delete(image);
        }
    }

    [Fact]
    public void WritesAreSectorAligned()
    {
        var device = new RecordingDevice();
        var layout = Fat32Layout.Compute(8 * GB, 4096, 256);
        Fat32Formatter.Write(device, layout, "X", 1);
        Assert.NotEmpty(device.Writes);
        Assert.All(device.Writes, w =>
        {
            Assert.Equal(0, w.Offset % 4096);
            Assert.Equal(0, w.Length % 4096);
        });
    }

    sealed class RecordingDevice : IBlockDevice
    {
        public List<(long Offset, int Length)> Writes { get; } = [];
        public void Write(long offset, ReadOnlySpan<byte> data) => Writes.Add((offset, data.Length));
        public void Flush() { }
    }
}

static class Tools
{
    static readonly string[] SearchPath =
        (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator)
        .Concat(["/opt/homebrew/sbin", "/opt/homebrew/bin", "/usr/sbin", "/sbin"]).ToArray();

    public static string? Find(string tool) =>
        SearchPath.Select(dir => Path.Combine(dir, tool)).FirstOrDefault(File.Exists);

    public static bool Have(string tool) => Find(tool) is not null;

    public static (int Status, string Output) Run(string tool, params string[] args)
    {
        var info = new ProcessStartInfo(Find(tool)!) { RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var arg in args) info.ArgumentList.Add(arg);
        info.Environment["MTOOLS_SKIP_CHECK"] = "1";
        using var process = Process.Start(info)!;
        var output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
        process.WaitForExit();
        return (process.ExitCode, output);
    }

    public static void AssertOk(this (int Status, string Output) result, string what) =>
        Assert.True(result.Status == 0, $"{what} failed ({result.Status}):\n{result.Output}");
}
