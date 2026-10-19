using StampCore;
using Xunit;

namespace StampCore.Tests;

public class ContentWriterTests
{
    [Fact]
    public void PreviewCountsFilesAndTopLevelNames()
    {
        var zip = Zips.Make();
        var preview = ContentWriter.Preview(zip);
        Assert.Equal(3, preview.FileCount);
        Assert.Equal(5000 + 5 + 25, preview.UncompressedBytes);
        Assert.Equal(["._readme.txt", "firmware", "readme.txt"], preview.TopLevelNames);
        Assert.Empty(preview.Warnings(1024 * 1024));
        Assert.Single(preview.Warnings(100));
    }

    [Fact]
    public void WarnsAboutFilesTooBigForFat32()
    {
        var preview = new ZipPreview(1, 5L << 30, ["big.iso"], 5L << 30);
        Assert.Contains(preview.Warnings(null), w => w.Contains("4 GB"));
    }

    [Fact]
    public void RejectsMissingCorruptAndEmptyZips()
    {
        Assert.Throws<ContentException>(() => ContentWriter.Preview("/nonexistent.zip"));
        var corrupt = Path.GetTempFileName();
        File.WriteAllText(corrupt, "not a zip");
        Assert.Contains("not a valid zip", Assert.Throws<ContentException>(() => ContentWriter.Preview(corrupt)).Message);
        var empty = Path.Combine(Path.GetTempPath(), $"stamp-empty-{Guid.NewGuid():N}.zip");
        System.IO.Compression.ZipFile.Open(empty, System.IO.Compression.ZipArchiveMode.Create).Dispose();
        Assert.Contains("empty", Assert.Throws<ContentException>(() => ContentWriter.Preview(empty)).Message);
    }

    [Fact]
    public void ExtractSkipsSymlinksAndKeepsDotUnderscoreFiles()
    {
        var zip = Zips.Make(withSymlink: true);
        var destination = Directory.CreateTempSubdirectory("stamp-x-").FullName;
        var fractions = new List<double>();
        var skipped = ContentWriter.Extract(zip, destination, fractions.Add);
        Assert.Equal(["latest"], skipped);
        Assert.False(File.Exists(Path.Combine(destination, "latest")));
        Assert.True(File.Exists(Path.Combine(destination, "._readme.txt")));
        Assert.Equal(5000, new FileInfo(Path.Combine(destination, "firmware", "boot.bin")).Length);
        Assert.Equal(1, fractions[^1]);
    }

    [Fact]
    public void ExtractRefusesPathsOutsideTheDestination()
    {
        var zip = Zips.Make(evilName: "../../escaped.txt");
        var destination = Directory.CreateTempSubdirectory("stamp-x-").FullName;
        Assert.Contains("outside", Assert.Throws<ContentException>(() => ContentWriter.Extract(zip, destination, _ => { })).Message);
    }

    [Fact]
    public void CopyTreeCopiesEverythingAndReportsProgress()
    {
        var source = Directory.CreateTempSubdirectory("stamp-src-").FullName;
        ContentWriter.Extract(Zips.Make(), source, _ => { });
        var card = Directory.CreateTempSubdirectory("stamp-card-").FullName;
        var fractions = new List<double>();
        ContentWriter.CopyTree(source, card, fractions.Add);
        Assert.Equal("hello", File.ReadAllText(Path.Combine(card, "readme.txt")));
        Assert.True(File.Exists(Path.Combine(card, "firmware", "boot.bin")));
        Assert.Equal(1, fractions[^1]);
        Assert.Empty(ContentWriter.MissingTopLevel(card, ["firmware", "readme.txt"], []));
        Assert.Equal(["gone"], ContentWriter.MissingTopLevel(card, ["readme.txt", "gone"], []));
        Assert.Empty(ContentWriter.MissingTopLevel(card, ["latest"], ["latest"]));
    }
}
