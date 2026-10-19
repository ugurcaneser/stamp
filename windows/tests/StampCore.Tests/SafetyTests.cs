using StampCore;
using Xunit;

namespace StampCore.Tests;

public class SafetyTests
{
    [Fact]
    public void ParsesTheCannedDiskList()
    {
        var disks = Fixtures.Disks();
        Assert.Equal([0, 1, 2, 3], disks.Select(d => d.Number));
        var card = Fixtures.Card;
        Assert.True(card.Removable);
        Assert.Equal("USB", card.BusType);
        Assert.Equal(['E'], card.DriveLetters);
        Assert.Equal(@"\\.\PhysicalDrive1", card.DevicePath);
        Assert.False(disks.Single(d => d.Number == 2).Removable);
    }

    [Fact]
    public void ParsesASingleObjectAndEmptyOutput()
    {
        var one = DiskScanner.Parse("""{"Number":5,"Size":1024,"MediaType":"Removable Media","DriveLetters":"H"}""");
        Assert.Equal(['H'], Assert.Single(one).DriveLetters);
        Assert.Empty(DiskScanner.Parse(""));
        Assert.Throws<DiskScanException>(() => DiskScanner.Parse("not json"));
    }

    [Fact]
    public void BootDiskAndVirtualDisksAreNeverCandidates()
    {
        var candidates = Safety.Candidates(Fixtures.Disks(), 'C');
        Assert.Equal([1, 2], candidates.Select(d => d.Number));
    }

    [Fact]
    public void DiskHoldingTheSystemDriveIsBlockedEvenWithoutBootFlags()
    {
        var disk = Fixtures.Card with { DriveLetters = ['D'] };
        var check = Safety.Evaluate(disk, 'D', allowNonRemovable: true);
        Assert.False(check.Ok);
        Assert.Contains("running Windows", check.BlockingReasons[0]);
    }

    [Fact]
    public void RemovableCardIsAllowed()
    {
        var check = Safety.Evaluate(Fixtures.Card, 'C');
        Assert.True(check.Ok);
        Assert.Empty(check.Warnings);
    }

    [Fact]
    public void NonRemovableNeedsOverride()
    {
        var hdd = Fixtures.Disks().Single(d => d.Number == 2) with { SizeBytes = 500L * 1024 * 1024 * 1024 };
        var refused = Safety.Evaluate(hdd, 'C');
        Assert.False(refused.Ok);
        Assert.True(refused.RequiresOverride);

        var allowed = Safety.Evaluate(hdd, 'C', allowNonRemovable: true);
        Assert.True(allowed.Ok);
        Assert.Single(allowed.Warnings);
    }

    [Fact]
    public void ReadOnlyAndHugeDisksAreRefused()
    {
        Assert.False(Safety.Evaluate(Fixtures.Card with { IsReadOnly = true }, 'C').Ok);
        var huge = Safety.Evaluate(Fixtures.Card with { SizeBytes = 3L * 1024 * 1024 * 1024 * 1024 }, 'C');
        Assert.False(huge.Ok);
        Assert.Contains("2 TB", huge.BlockingReasons[0]);
    }

    [Fact]
    public void ScannerUsesAnEncodedPowerShellCommand()
    {
        var runner = new FakeRunner(Fixtures.Read("get-disk.json"));
        Assert.Equal(4, new DiskScanner(runner).Scan().Count);
        var (exe, args) = Assert.Single(runner.Calls);
        Assert.Equal("powershell.exe", exe);
        var encoded = args[args.ToList().IndexOf("-EncodedCommand") + 1];
        var script = System.Text.Encoding.Unicode.GetString(Convert.FromBase64String(encoded));
        Assert.Contains("Get-Disk", script);
        Assert.Contains("Win32_DiskDrive", script);
    }

    [Fact]
    public void DiskPartScriptTargetsOnlyTheChosenDisk()
    {
        var script = WindowsDiskTools.PartitionScript(7);
        Assert.StartsWith("select disk 7\r\n", script);
        Assert.Contains("create partition primary align=1024 id=0c", script);
        Assert.DoesNotContain("format", script);  // diskpart refuses FAT32 above 32 GB; Stamp formats it itself
    }
}
