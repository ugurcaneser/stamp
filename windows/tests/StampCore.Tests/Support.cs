using StampCore;

namespace StampCore.Tests;

static class Fixtures
{
    public static string Read(string name) => File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", name));

    public static IReadOnlyList<Disk> Disks() => DiskScanner.Parse(Read("get-disk.json"));

    public static Disk Card => Disks().Single(d => d.Number == 1);
}

/// <summary>Answers every powershell call with the next canned JSON (the last one repeats).</summary>
sealed class FakeRunner(params string[] scans) : ICommandRunner
{
    public List<(string Executable, IReadOnlyList<string> Arguments)> Calls { get; } = [];
    int next;

    public CommandResult Run(string executable, IReadOnlyList<string> arguments, string? stdin = null)
    {
        Calls.Add((executable, arguments));
        var json = scans[Math.Min(next++, scans.Length - 1)];
        return new CommandResult(0, json, "");
    }
}

sealed class FakeDiskTools : IDiskTools
{
    public string Root { get; } = Directory.CreateTempSubdirectory("stamp-card-").FullName;
    public List<string> Calls { get; } = [];
    public bool EjectSucceeds { get; set; } = true;

    public char Partition(Disk disk)
    {
        Calls.Add($"partition {disk.Number}");
        return 'E';
    }

    public void FormatFat32(char letter, string label, Action<double> progress)
    {
        Calls.Add($"format {letter} {label}");
        progress(1);
    }

    public string WaitForFat32(char letter)
    {
        Calls.Add($"wait {letter}");
        return Root;
    }

    public void Flush(char letter) => Calls.Add($"flush {letter}");

    public bool Eject(char letter)
    {
        Calls.Add($"eject {letter}");
        return EjectSucceeds;
    }
}

static class Zips
{
    /// <summary>A zip with a folder, files and optionally a Unix symlink entry.</summary>
    public static string Make(bool withSymlink = false, string? evilName = null)
    {
        var path = Path.Combine(Path.GetTempPath(), $"stamp-test-{Guid.NewGuid():N}.zip");
        using var archive = System.IO.Compression.ZipFile.Open(path, System.IO.Compression.ZipArchiveMode.Create);
        archive.CreateEntry("firmware/");
        Write(archive, "firmware/boot.bin", new string('x', 5000));
        Write(archive, "readme.txt", "hello");
        Write(archive, "._readme.txt", "apple double kept as data");
        if (withSymlink)
        {
            var link = archive.CreateEntry("latest");
            link.ExternalAttributes = unchecked((int)(0xA1FFu << 16));  // S_IFLNK | 0777
            using var writer = new StreamWriter(link.Open());
            writer.Write("firmware/boot.bin");
        }
        if (evilName is not null) Write(archive, evilName, "gotcha");
        return path;
    }

    static void Write(System.IO.Compression.ZipArchive archive, string name, string content)
    {
        using var writer = new StreamWriter(archive.CreateEntry(name).Open());
        writer.Write(content);
    }
}
