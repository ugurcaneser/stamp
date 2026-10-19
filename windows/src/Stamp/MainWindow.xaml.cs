using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Threading;
using Microsoft.Win32;

namespace Stamp;

public partial class MainWindow : Window
{
    const int WmDeviceChange = 0x0219;

    readonly MainViewModel model = new();
    readonly DispatcherTimer rescan;

    public MainWindow()
    {
        InitializeComponent();
        DataContext = model;

        // Inserting a card fires a burst of WM_DEVICECHANGE; collapse it into one scan.
        rescan = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(700) };
        rescan.Tick += (_, _) =>
        {
            rescan.Stop();
            if (!model.IsRunning) model.Refresh();
        };
        SourceInitialized += (_, _) => HwndSource.FromHwnd(new WindowInteropHelper(this).Handle).AddHook(WndProc);
        model.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(MainViewModel.Failure) && model.Failure is not null) DetailsExpander.IsExpanded = true;
        };
        model.Refresh();
    }

    IntPtr WndProc(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message == WmDeviceChange)
        {
            rescan.Stop();
            rescan.Start();
        }
        return IntPtr.Zero;
    }

    void OnRefreshClicked(object sender, RoutedEventArgs e) => model.Refresh();

    void OnChooseZipClicked(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog { Filter = "Zip files (*.zip)|*.zip", Title = "Choose a zip file" };
        if (dialog.ShowDialog(this) == true) model.ChooseZip(dialog.FileName);
    }

    static string? DroppedZip(DragEventArgs e) =>
        e.Data.GetData(DataFormats.FileDrop) is string[] { Length: > 0 } files
        && string.Equals(Path.GetExtension(files[0]), ".zip", StringComparison.OrdinalIgnoreCase)
            ? files[0]
            : null;

    void OnDragEnter(object sender, DragEventArgs e) => OnDragOver(sender, e);

    void OnDragOver(object sender, DragEventArgs e)
    {
        var ok = DroppedZip(e) is not null;
        e.Effects = ok ? DragDropEffects.Copy : DragDropEffects.None;
        DropZone.Background = (System.Windows.Media.Brush)FindResource(ok ? "AccentSoftBrush" : "FillBrush");
        e.Handled = true;
    }

    void OnDragLeave(object sender, DragEventArgs e) => DropZone.Background = (System.Windows.Media.Brush)FindResource("FillBrush");

    void OnDrop(object sender, DragEventArgs e)
    {
        OnDragLeave(sender, e);
        if (DroppedZip(e) is { } zip) model.ChooseZip(zip);
    }

    void OnFormatClicked(object sender, RoutedEventArgs e)
    {
        if (model.SelectedDisk is not { } disk || model.ZipPath is not { } zip || model.Preview is not { } preview) return;
        var confirm = new ConfirmWindow(disk, zip, preview, model.SanitizedLabel) { Owner = this };
        if (confirm.ShowDialog() == true) model.Start(allowNonRemovable: !disk.Removable && confirm.AcceptedNonRemovable);
    }

    void OnBackClicked(object sender, RoutedEventArgs e)
    {
        DetailsExpander.IsExpanded = false;
        model.BackToSelection();
    }

    void OnLogChanged(object sender, TextChangedEventArgs e) => ((TextBox)sender).ScrollToEnd();
}
