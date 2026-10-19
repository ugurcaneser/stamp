using System.IO.Compression;

namespace StampCore;

public sealed class ContentException(string message) : Exception(message);

public sealed record ZipPreview(int FileCount, long UncompressedBytes, IReadOnlyList<string> TopLevelNames, long LargestFileBytes)
{
    /// <summary>FAT32 stores files up to 4 GiB - 1 byte.</summary>
    public const long MaxFat32FileBytes = uint.MaxValue;

    /// <summary>Warns (does not block), like core/content_writer.py and ContentWriter.swift.</summary>
    public IReadOnlyList<string> Warnings(long? targetCapacity)
    {
        var warnings = new List<string>();
        if (targetCapacity is long capacity && UncompressedBytes > capacity)
            warnings.Add($"Uncompressed contents ({Format.HumanSize(UncompressedBytes)}) exceed the target's capacity ({Format.HumanSize(capacity)}).");
        if (LargestFileBytes > MaxFat32FileBytes)
            warnings.Add($"The zip holds a file of {Format.HumanSize(LargestFileBytes)}; FAT32 can't store files of 4 GB or more.");
        return warnings;
    }
}

/// <summary>
/// Zip preview/extraction with System.IO.Compression, and the copy onto the
/// card. Extraction checks every entry stays inside the destination and skips
/// Unix symlink entries (FAT32 can't store links, and .NET would otherwise
/// write them out as small text files).
/// </summary>
public static class ContentWriter
{
    const int UnixFileTypeMask = 0xF000, UnixSymlink = 0xA000;

    public static ZipPreview Preview(string zipPath)
    {
        if (!File.Exists(zipPath))
            throw new ContentException($"{zipPath} does not exist or is not a file");
        try
        {
            using var archive = ZipFile.OpenRead(zipPath);
            var files = archive.Entries.Where(e => !IsDirectory(e)).ToList();
            if (files.Count == 0)
                throw new ContentException($"{Path.GetFileName(zipPath)} is empty");
            var topLevel = archive.Entries
                .Select(e => e.FullName.Replace('\\', '/').Split('/', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault())
                .Where(n => n is not null && n != ".")
                .Select(n => n!)
                .Distinct()
                .Order(StringComparer.Ordinal)
                .ToList();
            return new ZipPreview(files.Count, files.Sum(e => e.Length), topLevel, files.Max(e => e.Length));
        }
        catch (InvalidDataException exc)
        {
            throw new ContentException($"{Path.GetFileName(zipPath)} is not a valid zip file or is corrupt: {exc.Message}");
        }
    }

    /// <summary>Extracts into <paramref name="destination"/>; returns the entries skipped as symlinks.</summary>
    public static IReadOnlyList<string> Extract(string zipPath, string destination, Action<double> progress)
    {
        var root = Path.GetFullPath(destination);
        Directory.CreateDirectory(root);
        var rootWithSeparator = root.EndsWith(Path.DirectorySeparatorChar) ? root : root + Path.DirectorySeparatorChar;
        var skipped = new List<string>();
        try
        {
            using var archive = ZipFile.OpenRead(zipPath);
            var entries = archive.Entries;
            progress(0);
            for (var i = 0; i < entries.Count; i++)
            {
                var entry = entries[i];
                var target = Path.GetFullPath(Path.Combine(root, entry.FullName.Replace('\\', '/')));
                if (!target.StartsWith(rootWithSeparator, StringComparison.OrdinalIgnoreCase) && target != root)
                    throw new ContentException($"refusing to extract {entry.FullName}: it points outside the destination");

                if (IsDirectory(entry))
                {
                    Directory.CreateDirectory(target);
                }
                else if (((entry.ExternalAttributes >> 16) & UnixFileTypeMask) == UnixSymlink)
                {
                    skipped.Add(entry.FullName);
                    StampLog.Warning($"skipping {entry.FullName}: FAT32 can only store regular files and folders");
                }
                else
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                    entry.ExtractToFile(target, overwrite: true);
                }
                progress((i + 1.0) / entries.Count);
            }
        }
        catch (InvalidDataException exc)
        {
            throw new ContentException($"failed to extract {Path.GetFileName(zipPath)}: {exc.Message}");
        }
        catch (IOException exc)
        {
            throw new ContentException($"failed to extract {Path.GetFileName(zipPath)}: {exc.Message}");
        }
        progress(1);
        return skipped;
    }

    /// <summary>Copies the contents of <paramref name="source"/> into <paramref name="destination"/>, reporting by bytes.</summary>
    public static void CopyTree(string source, string destination, Action<double> progress)
    {
        var files = Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories).ToList();
        var total = Math.Max(files.Sum(f => new FileInfo(f).Length), 1);
        long copied = 0;
        var buffer = new byte[1024 * 1024];
        progress(0);

        foreach (var directory in Directory.EnumerateDirectories(source, "*", SearchOption.AllDirectories))
            Directory.CreateDirectory(Path.Combine(destination, Path.GetRelativePath(source, directory)));

        foreach (var file in files)
        {
            var relative = Path.GetRelativePath(source, file);
            try
            {
                using var input = File.OpenRead(file);
                using var output = File.Create(Path.Combine(destination, relative));
                int read;
                while ((read = input.Read(buffer)) > 0)
                {
                    output.Write(buffer, 0, read);
                    copied += read;
                    progress(Math.Min((double)copied / total, 1));
                }
            }
            catch (Exception exc) when (exc is IOException or UnauthorizedAccessException)
            {
                throw new ContentException($"copying {relative} failed: {exc.Message}");
            }
        }
        progress(1);
        StampLog.Info($"copied {files.Count} files from {source} to {destination}");
    }

    /// <summary>Spot-check that the expected top-level entries exist after the copy.</summary>
    public static IReadOnlyList<string> MissingTopLevel(string root, IEnumerable<string> expected, IReadOnlyCollection<string> skipped)
    {
        var actual = Directory.EnumerateFileSystemEntries(root).Select(Path.GetFileName).ToHashSet(StringComparer.OrdinalIgnoreCase);
        // A top-level symlink was skipped on purpose; it isn't missing.
        var skippedTop = skipped.Select(s => s.Replace('\\', '/').TrimEnd('/')).Where(s => !s.Contains('/')).ToHashSet();
        return expected.Where(name => !actual.Contains(name) && !skippedTop.Contains(name)).ToList();
    }

    static bool IsDirectory(ZipArchiveEntry entry) =>
        entry.FullName.EndsWith('/') || entry.FullName.EndsWith('\\');
}
