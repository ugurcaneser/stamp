using System.IO;
using System.Windows;
using StampCore;

namespace Stamp;

/// <summary>
/// The single "Erase &amp; Copy" gate, same shape as gui/confirm_dialog.py and
/// ConfirmSheet in the macOS app: one summary, plus one risk checkbox only for
/// disks not flagged removable.
/// </summary>
public partial class ConfirmWindow : Window
{
    readonly Disk disk;

    public ConfirmWindow(Disk disk, string zipPath, ZipPreview preview, string label)
    {
        InitializeComponent();
        this.disk = disk;
        Heading.Text = $"Erase {disk.DisplayModel}?";
        CardText.Text = $"{disk.DisplayModel} · {disk.SizeHuman} · {disk.Details}";
        ZipText.Text = $"{Path.GetFileName(zipPath)} · {preview.FileCount} files · {Format.HumanSize(preview.UncompressedBytes)}";
        NameText.Text = label;
        if (!disk.Removable)
        {
            NonRemovableBox.Visibility = Visibility.Visible;
            EraseButton.IsEnabled = false;
        }
    }

    public bool AcceptedNonRemovable => NonRemovableBox.IsChecked == true;

    void OnRiskChanged(object sender, RoutedEventArgs e) => EraseButton.IsEnabled = disk.Removable || AcceptedNonRemovable;

    void OnEraseClicked(object sender, RoutedEventArgs e) => DialogResult = true;
}
