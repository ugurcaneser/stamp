using System.Diagnostics;

namespace StampCore;

public sealed record CommandResult(int Status, string Stdout, string Stderr)
{
    public string Detail => string.IsNullOrWhiteSpace(Stderr) ? Stdout.Trim() : Stderr.Trim();
}

/// <summary>Every external tool (powershell, diskpart) goes through this, so tests can fake it.</summary>
public interface ICommandRunner
{
    CommandResult Run(string executable, IReadOnlyList<string> arguments, string? stdin = null);
}

public sealed class ProcessRunner : ICommandRunner
{
    public CommandResult Run(string executable, IReadOnlyList<string> arguments, string? stdin = null)
    {
        var info = new ProcessStartInfo(executable)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = stdin is not null,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var argument in arguments) info.ArgumentList.Add(argument);
        StampLog.Info($"run: {executable} {string.Join(' ', arguments.Select(a => a.Length > 120 ? "<script>" : a))}");

        using var process = Process.Start(info) ?? throw new InvalidOperationException($"could not start {executable}");
        if (stdin is not null)
        {
            process.StandardInput.Write(stdin);
            process.StandardInput.Close();
        }
        var stderrTask = process.StandardError.ReadToEndAsync();
        var stdout = process.StandardOutput.ReadToEnd();
        process.WaitForExit();
        var result = new CommandResult(process.ExitCode, stdout, stderrTask.Result);
        if (result.Status != 0) StampLog.Warning($"{Path.GetFileName(executable)} exited {result.Status}: {result.Detail}");
        return result;
    }
}
