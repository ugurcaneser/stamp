using StampCore;
using Xunit;

namespace StampCore.Tests;

public class StampJobTests
{
    static readonly string DiskList = Fixtures.Read("get-disk.json");

    [Fact]
    public void RunsTheWholeFlowInOrder()
    {
        var tools = new FakeDiskTools();
        var job = new StampJob(new DiskScanner(new FakeRunner(DiskList)), tools, () => 'C');
        var phases = new List<Phase>();
        var result = job.Run(new JobRequest(Fixtures.Card, Zips.Make(withSymlink: true), "my card", false),
            (phase, _, _) => { if (phases.LastOrDefault() != phase || phases.Count == 0) phases.Add(phase); });

        Assert.Equal(["partition 1", "format E MY CARD", "wait E", "flush E", "eject E"], tools.Calls);
        Assert.Equal(Enum.GetValues<Phase>(), phases.Distinct());
        Assert.Equal("MY CARD", result.Label);
        Assert.Equal(["latest"], result.Skipped);
        Assert.True(result.Ejected);
        Assert.Equal("hello", File.ReadAllText(Path.Combine(tools.Root, "readme.txt")));
    }

    [Fact]
    public void BadZipNeverTouchesTheDisk()
    {
        var tools = new FakeDiskTools();
        var job = new StampJob(new DiskScanner(new FakeRunner(DiskList)), tools, () => 'C');
        var corrupt = Path.GetTempFileName();
        File.WriteAllText(corrupt, "nope");
        Assert.Throws<ContentException>(() => job.Run(new JobRequest(Fixtures.Card, corrupt, "X", false), (_, _, _) => { }));
        Assert.Empty(tools.Calls);
    }

    [Fact]
    public void RefusesWhenTheCardWasSwapped()
    {
        var swapped = DiskList.Replace("\"SerialNumber\":\"000000000820\"", "\"SerialNumber\":\"999\"");
        var tools = new FakeDiskTools();
        var job = new StampJob(new DiskScanner(new FakeRunner(swapped)), tools, () => 'C');
        var error = Assert.Throws<JobException>(() => job.Run(new JobRequest(Fixtures.Card, Zips.Make(), "X", false), (_, _, _) => { }));
        Assert.Contains("changed since it was selected", error.Message);
        Assert.Empty(tools.Calls);
    }

    [Fact]
    public void RefusesWhenTheCardIsGone()
    {
        var tools = new FakeDiskTools();
        var job = new StampJob(new DiskScanner(new FakeRunner("[]")), tools, () => 'C');
        Assert.Throws<JobException>(() => job.Run(new JobRequest(Fixtures.Card, Zips.Make(), "X", false), (_, _, _) => { }));
        Assert.Empty(tools.Calls);
    }

    [Fact]
    public void NonRemovableDiskNeedsTheOverride()
    {
        var hdd = Fixtures.Disks().Single(d => d.Number == 2);
        var small = DiskList.Replace("2000365289472", "500107862016");
        hdd = hdd with { SizeBytes = 500107862016 };
        var tools = new FakeDiskTools();
        var job = new StampJob(new DiskScanner(new FakeRunner(small)), tools, () => 'C');
        Assert.Throws<SafetyException>(() => job.Run(new JobRequest(hdd, Zips.Make(), "X", false), (_, _, _) => { }));
        Assert.Empty(tools.Calls);

        job.Run(new JobRequest(hdd, Zips.Make(), "X", true), (_, _, _) => { });
        Assert.Contains("partition 2", tools.Calls);
    }

    [Fact]
    public void FailedEjectIsAWarningNotAFailure()
    {
        var tools = new FakeDiskTools { EjectSucceeds = false };
        var job = new StampJob(new DiskScanner(new FakeRunner(DiskList)), tools, () => 'C');
        var messages = new List<string>();
        var result = job.Run(new JobRequest(Fixtures.Card, Zips.Make(), "X", false), (_, _, m) => { if (m is not null) messages.Add(m); });
        Assert.False(result.Ejected);
        Assert.Contains(messages, m => m.StartsWith("Warning: could not eject"));
    }
}
