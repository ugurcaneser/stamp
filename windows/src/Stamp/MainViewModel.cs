using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Windows.Media;
using StampCore;

namespace Stamp;

public abstract class Observable : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;

    protected void Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        Raise(name);
    }

    protected void Raise(params string?[] names)
    {
        foreach (var name in names) PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}

public enum PhaseState { Pending, Active, Done, Failed }

public sealed class PhaseItem(Phase phase) : Observable
{
    PhaseState state;

    public Phase Phase { get; } = phase;
    public string Title => Phase.Title();

    public PhaseState State
    {
        get => state;
        set
        {
            Set(ref state, value);
            Raise(nameof(Glyph), nameof(GlyphBrush), nameof(TextBrush));
        }
    }

    // Segoe Fluent Icons / Segoe MDL2 Assets code points.
    public string Glyph => State switch
    {
        PhaseState.Active => "",  // Sync
        PhaseState.Done => "",    // CheckMark
        PhaseState.Failed => "",  // Cancel
        _ => "",                  // CircleRing
    };

    public Brush GlyphBrush => State switch
    {
        PhaseState.Active => Theme.Accent,
        PhaseState.Done => Theme.Success,
        PhaseState.Failed => Theme.Danger,
        _ => Theme.Subtle,
    };

    public Brush TextBrush => State == PhaseState.Pending ? Theme.Secondary : Theme.Primary;
}

public static class Theme
{
    public static Brush Accent => Lookup("AccentBrush");
    public static Brush Success => Lookup("SuccessBrush");
    public static Brush Danger => Lookup("DangerBrush");
    public static Brush Subtle => Lookup("SubtleBrush");
    public static Brush Primary => Lookup("TextBrush");
    public static Brush Secondary => Lookup("SecondaryTextBrush");

    static Brush Lookup(string key) => (Brush)Application.Current.Resources[key];
}

/// <summary>
/// UI state for the whole app, the WPF counterpart of AppModel.swift. Disk
/// scans, zip checks and the job run on the thread pool and publish back on
/// the dispatcher, so the window never blocks.
/// </summary>
public sealed class MainViewModel : Observable
{
    readonly DiskScanner scanner = new();
    readonly System.Windows.Threading.Dispatcher dispatcher = Application.Current.Dispatcher;
    readonly char? systemDrive = Safety.SystemDriveLetter();

    Disk? selectedDisk;
    string? scanError;
    string? zipPath;
    ZipPreview? preview;
    string? zipError;
    bool isCheckingZip;
    string label = Fat32Formatter.DefaultLabel;
    bool isRunning;
    double overallFraction;
    string logText = "";
    JobResult? result;
    string? failure;
    Phase? activePhase;

    public ObservableCollection<Disk> Disks { get; } = [];
    public ObservableCollection<PhaseItem> Phases { get; } = new(Enum.GetValues<Phase>().Select(p => new PhaseItem(p)));

    // ----- select screen -----

    public Disk? SelectedDisk
    {
        get => selectedDisk;
        set
        {
            Set(ref selectedDisk, value);
            Raise(nameof(CanFormat), nameof(FormatHint), nameof(ZipWarnings));
        }
    }

    public string? ScanError { get => scanError; private set => Set(ref scanError, value); }
    public bool HasNoDisks => Disks.Count == 0;

    public string? ZipPath
    {
        get => zipPath;
        private set
        {
            Set(ref zipPath, value);
            Raise(nameof(HasZip), nameof(ZipName), nameof(ZipSubtitle));
        }
    }

    public bool HasZip => ZipPath is not null;
    public string ZipName => ZipPath is null ? "" : Path.GetFileName(ZipPath);

    public string ZipSubtitle => IsCheckingZip ? "Checking…"
        : preview is null ? "Can't be used"
        : $"{preview.FileCount} files · {Format.HumanSize(preview.UncompressedBytes)}";

    public ZipPreview? Preview
    {
        get => preview;
        private set
        {
            Set(ref preview, value);
            Raise(nameof(CanFormat), nameof(FormatHint), nameof(ZipWarnings), nameof(ZipSubtitle));
        }
    }

    public string? ZipError { get => zipError; private set => Set(ref zipError, value); }

    public bool IsCheckingZip
    {
        get => isCheckingZip;
        private set
        {
            Set(ref isCheckingZip, value);
            Raise(nameof(CanFormat), nameof(FormatHint), nameof(ZipSubtitle));
        }
    }

    public IReadOnlyList<string> ZipWarnings => Preview?.Warnings(SelectedDisk?.SizeBytes) ?? [];

    public string Label
    {
        get => label;
        set
        {
            Set(ref label, value);
            Raise(nameof(LabelHint));
        }
    }

    public string SanitizedLabel => Fat32Formatter.SanitizeLabel(Label);

    public string LabelHint => SanitizedLabel != Label
        ? $"Will be written as {SanitizedLabel}"
        : "Shown as the card's name. Up to 11 letters or digits.";

    public bool CanFormat => SelectedDisk is not null && Preview is not null && !IsCheckingZip;

    /// <summary>One line next to the Format button saying what's still missing.</summary>
    public string FormatHint =>
        SelectedDisk is null ? "Insert a card to continue."
        : IsCheckingZip ? "Checking the zip file…"
        : Preview is null ? "Choose a zip file to continue."
        : "Everything on the card will be erased.";

    // ----- progress screen -----

    public bool IsRunning
    {
        get => isRunning;
        private set
        {
            Set(ref isRunning, value);
            Raise(nameof(ShowSelect), nameof(ShowProgress));
        }
    }

    public bool ShowProgress => IsRunning || Result is not null || Failure is not null;
    public bool ShowSelect => !ShowProgress;
    /// <summary>The phase list shows while running and after a failure, not on success.</summary>
    public bool ShowPhases => Result is null;
    public double OverallFraction { get => overallFraction; private set => Set(ref overallFraction, value); }
    public string ActivePhaseTitle => activePhase?.Title() ?? "Starting";
    public string LogText { get => logText; private set => Set(ref logText, value); }

    public JobResult? Result
    {
        get => result;
        private set
        {
            Set(ref result, value);
            Raise(nameof(ShowSelect), nameof(ShowProgress), nameof(ShowPhases), nameof(ResultMessage), nameof(SkippedMessage), nameof(HasSkipped));
        }
    }

    public string? Failure
    {
        get => failure;
        private set
        {
            Set(ref failure, value);
            Raise(nameof(ShowSelect), nameof(ShowProgress));
        }
    }

    public string ResultMessage => Result is null ? ""
        : Result.Ejected ? $"{Result.Label} was ejected and is safe to remove."
        : $"Use \"Safely Remove Hardware\" for {Result.Label} before removing it.";

    public bool HasSkipped => Result is { Skipped.Count: > 0 };
    public string SkippedMessage => Result is null ? "" : $"Skipped symlinks (FAT32 can't store them): {string.Join(", ", Result.Skipped)}";

    // ----- devices -----

    int refreshGeneration;

    public void Refresh()
    {
        var generation = ++refreshGeneration;
        Task.Run(() =>
        {
            try
            {
                var disks = Safety.Candidates(scanner.Scan(), systemDrive);
                dispatcher.BeginInvoke(() => { if (generation == refreshGeneration) ApplyDisks(disks); });
            }
            catch (Exception exc) when (exc is DiskScanException or System.ComponentModel.Win32Exception or InvalidOperationException)
            {
                dispatcher.BeginInvoke(() => ScanError = exc.Message);
            }
        });
    }

    void ApplyDisks(IReadOnlyList<Disk> disks)
    {
        var previous = SelectedDisk;
        Disks.Clear();
        foreach (var disk in disks) Disks.Add(disk);
        ScanError = null;
        SelectedDisk = previous is null ? null : disks.FirstOrDefault(d => d.SameMedia(previous));
        SelectedDisk ??= disks.FirstOrDefault(d => d.Removable);
        Raise(nameof(HasNoDisks));
    }

    // ----- zip -----

    public void ChooseZip(string path)
    {
        ZipPath = path;
        Preview = null;
        ZipError = null;
        IsCheckingZip = true;
        StampLog.Info($"zip selected: {path}");
        Task.Run(() =>
        {
            ZipPreview? checkedPreview = null;
            string? error = null;
            try
            {
                checkedPreview = ContentWriter.Preview(path);
            }
            catch (ContentException exc)
            {
                error = exc.Message;
            }
            catch (IOException exc)
            {
                error = exc.Message;
            }
            dispatcher.BeginInvoke(() =>
            {
                if (ZipPath != path) return;
                IsCheckingZip = false;
                Preview = checkedPreview;
                ZipError = error;
            });
        });
    }

    // ----- job -----

    public void Start(bool allowNonRemovable)
    {
        if (SelectedDisk is not { } disk || ZipPath is not { } zip) return;
        StampLog.Info($"device selected: {disk.DisplayName} ({disk.DisplayModel}, {disk.SizeHuman}, removable={disk.Removable})");
        foreach (var phase in Phases) phase.State = PhaseState.Pending;
        activePhase = null;
        OverallFraction = 0;
        LogText = "";
        Result = null;
        Failure = null;
        Raise(nameof(ActivePhaseTitle));
        IsRunning = true;

        var request = new JobRequest(disk, zip, Label, allowNonRemovable);
        var job = new StampJob(scanner, new WindowsDiskTools(new ProcessRunner(), scanner), () => systemDrive);
        Task.Run(() =>
        {
            try
            {
                var jobResult = job.Run(request, (phase, fraction, message) =>
                    dispatcher.BeginInvoke(() => Update(phase, fraction, message)));
                dispatcher.BeginInvoke(() =>
                {
                    foreach (var phase in Phases) phase.State = PhaseState.Done;
                    OverallFraction = 1;
                    IsRunning = false;
                    Result = jobResult;
                    Refresh();
                });
            }
            catch (Exception exc)
            {
                StampLog.Error($"job failed: {exc}");
                dispatcher.BeginInvoke(() =>
                {
                    if (activePhase is Phase phase) Phases[(int)phase].State = PhaseState.Failed;
                    AppendLog($"Error: {exc.Message}");
                    IsRunning = false;
                    Failure = exc.Message;
                    Refresh();
                });
            }
        });
    }

    void Update(Phase phase, double? fraction, string? message)
    {
        if (activePhase != phase)
        {
            foreach (var item in Phases.Where(p => p.Phase < phase)) item.State = PhaseState.Done;
            Phases[(int)phase].State = PhaseState.Active;
            activePhase = phase;
            Raise(nameof(ActivePhaseTitle));
        }
        // Each phase is an equal slice of the bar.
        OverallFraction = ((int)phase + (fraction ?? 0)) / Phases.Count;
        if (message is not null) AppendLog(message);
    }

    void AppendLog(string line) => LogText = LogText.Length == 0 ? line : LogText + Environment.NewLine + line;

    public void BackToSelection()
    {
        Result = null;
        Failure = null;
    }
}
