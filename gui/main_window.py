"""Main application window: ties device_picker + zip selection + confirm_dialog
+ progress_view together, and runs the actual format/copy work on a
background thread so the GTK main loop never blocks (§5).
"""

from __future__ import annotations

import os
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from core import content_writer, formatter, logging_setup, safety
from core.content_writer import ContentError, ZipPreview
from core.formatter import DEFAULT_LABEL, FormatError
from core.models import Device, human_size
from core.safety import SafetyError

from gui.confirm_dialog import ConfirmDialog, confirm_non_removable_override
from gui.device_picker import DevicePicker
from gui.progress_view import ProgressView

log = logging_setup.get_logger()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Stamp", default_width=720, default_height=680)

        self._zip_path: str | None = None
        self._zip_preview: ZipPreview | None = None
        self._non_removable_override_granted = False

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar_view.set_content(self._stack)
        self.set_content(toolbar_view)

        self._build_select_page()
        self._build_progress_page()
        self._stack.set_visible_child_name("select")

    # --- "select device + zip" page -------------------------------------------------

    def _build_select_page(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(16)
        page.set_margin_bottom(16)
        page.set_margin_start(16)
        page.set_margin_end(16)

        self._device_picker = DevicePicker()
        self._device_picker.connect("device-selected", self._on_device_selected)
        page.append(self._device_picker)

        zip_group = Adw.PreferencesGroup(title="Zip contents")
        page.append(zip_group)

        self._zip_row = Adw.ActionRow(title="No zip file selected", subtitle="Choose a .zip to copy onto the card")
        choose_button = Gtk.Button(label="Choose zip file…")
        choose_button.set_valign(Gtk.Align.CENTER)
        choose_button.connect("clicked", self._on_choose_zip_clicked)
        self._zip_row.add_suffix(choose_button)
        zip_group.add(self._zip_row)

        label_group = Adw.PreferencesGroup(title="Volume label")
        self._label_row = Adw.EntryRow(title="FAT32 label (max 11 characters)")
        self._label_row.set_text(DEFAULT_LABEL)
        label_group.add(self._label_row)
        page.append(label_group)

        self._error_banner = Adw.Banner(title="", revealed=False)
        self._error_banner.add_css_class("error")
        page.append(self._error_banner)

        spacer = Gtk.Box(vexpand=True)
        page.append(spacer)

        self._format_button = Gtk.Button(label="Format & Copy…")
        self._format_button.add_css_class("destructive-action")
        self._format_button.add_css_class("pill")
        self._format_button.set_sensitive(False)
        self._format_button.set_halign(Gtk.Align.CENTER)
        self._format_button.connect("clicked", self._on_format_clicked)
        page.append(self._format_button)

        self._stack.add_named(page, "select")

        self._device_picker.start_auto_refresh()

    def _on_device_selected(self, _picker: DevicePicker, device: Device | None) -> None:
        self._non_removable_override_granted = False
        self._update_format_button_sensitivity()

    def _on_choose_zip_clicked(self, _button: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Choose a zip file")
        zip_filter = Gtk.FileFilter()
        zip_filter.set_name("Zip files")
        zip_filter.add_pattern("*.zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(zip_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(zip_filter)
        dialog.open(self, None, self._on_zip_dialog_done)

    def _on_zip_dialog_done(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # user cancelled
        if gfile is None:
            return
        path = gfile.get_path()
        if not path:
            return
        self._load_zip(path)

    def _load_zip(self, path: str) -> None:
        try:
            target_free = self._device_picker.selected_device.size_bytes if self._device_picker.selected_device else None
            preview = content_writer.preview_zip(path, target_free_bytes=target_free)
        except ContentError as exc:
            self._error_banner.set_title(str(exc))
            self._error_banner.set_revealed(True)
            self._zip_path = None
            self._zip_preview = None
            self._zip_row.set_title("No zip file selected")
            self._zip_row.set_subtitle("Choose a .zip to copy onto the card")
            self._update_format_button_sensitivity()
            return

        self._error_banner.set_revealed(False)
        self._zip_path = path
        self._zip_preview = preview
        self._zip_row.set_title(os.path.basename(path))
        subtitle = f"{preview.file_count} files, {human_size(preview.uncompressed_size_bytes)}"
        self._zip_row.set_subtitle(subtitle)

        if preview.warnings:
            self._error_banner.set_title(" ".join(preview.warnings))
            self._error_banner.set_revealed(True)

        self._update_format_button_sensitivity()

    def _update_format_button_sensitivity(self) -> None:
        device = self._device_picker.selected_device
        self._format_button.set_sensitive(device is not None and self._zip_path is not None)

    def _on_format_clicked(self, _button: Gtk.Button) -> None:
        device = self._device_picker.selected_device
        if device is None or self._zip_path is None or self._zip_preview is None:
            return

        root_disk = self._device_picker.root_disk_path or ""
        check = safety.evaluate_device_safety(
            device, root_disk, allow_non_removable_override=self._non_removable_override_granted
        )
        if not check.ok:
            if check.requires_override and not self._non_removable_override_granted:
                confirm_non_removable_override(self, device, self._on_non_removable_decision)
                return
            self._error_banner.set_title("; ".join(check.blocking_reasons))
            self._error_banner.set_revealed(True)
            return

        self._open_confirm_dialog(device)

    def _on_non_removable_decision(self, granted: bool) -> None:
        if not granted:
            return
        self._non_removable_override_granted = True
        device = self._device_picker.selected_device
        if device is not None:
            self._open_confirm_dialog(device)

    def _open_confirm_dialog(self, device: Device) -> None:
        assert self._zip_path is not None and self._zip_preview is not None
        dialog = ConfirmDialog(
            self,
            device,
            self._zip_path,
            self._zip_preview,
            self._label_row.get_text(),
            on_result=lambda confirmed: self._on_confirm_result(confirmed, device),
            non_removable_override_already_granted=self._non_removable_override_granted,
        )
        dialog.present()

    def _on_confirm_result(self, confirmed: bool, device: Device) -> None:
        if not confirmed:
            return
        self._start_prepare_flow(device)

    # --- progress page + background worker --------------------------------------------

    def _build_progress_page(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._progress_view = ProgressView()
        box.append(self._progress_view)

        self._start_over_button = Gtk.Button(label="Start Over")
        self._start_over_button.set_halign(Gtk.Align.CENTER)
        self._start_over_button.set_margin_bottom(16)
        self._start_over_button.set_visible(False)
        self._start_over_button.connect("clicked", lambda *_: self._return_to_select_page())
        box.append(self._start_over_button)

        self._stack.add_named(box, "progress")

    def _return_to_select_page(self) -> None:
        self._stack.set_visible_child_name("select")
        self._device_picker.refresh()

    def _start_prepare_flow(self, device: Device) -> None:
        assert self._zip_path is not None
        zip_path = self._zip_path
        label = self._label_row.get_text()
        root_disk = self._device_picker.root_disk_path or ""
        allow_override = self._non_removable_override_granted

        self._progress_view.reset()
        self._start_over_button.set_visible(False)
        self._stack.set_visible_child_name("progress")

        thread = threading.Thread(
            target=self._worker,
            args=(device, root_disk, zip_path, label, allow_override),
            daemon=True,
            name="stamp-prepare-worker",
        )
        thread.start()

    def _worker(self, device: Device, root_disk: str, zip_path: str, label: str, allow_override: bool) -> None:
        def status(msg: str) -> None:
            GLib.idle_add(self._progress_view.set_status, msg)
            GLib.idle_add(self._progress_view.log, msg)

        def phase_progress(phase: str, fraction: float) -> None:
            GLib.idle_add(self._progress_view.set_phase_fraction, phase, fraction)

        tmp_extract = tempfile.mkdtemp(prefix="stamp-extract-")
        mount_path: str | None = None
        partition_path: str | None = None
        try:
            result = formatter.format_device(
                device,
                root_disk,
                label=label,
                allow_non_removable_override=allow_override,
                progress_cb=status,
            )
            partition_path = result.partition_path

            status("Extracting zip…")
            content_writer.extract_zip(zip_path, tmp_extract, progress_cb=phase_progress)

            status(f"Mounting {partition_path}…")
            mount_path = formatter.mount_partition(partition_path, progress=status)

            status("Copying files onto the card…")
            content_writer.copy_tree_rsync(tmp_extract, mount_path, progress_cb=phase_progress)

            status("Flushing writes to the card…")
            content_writer.sync_and_flush()

            top_level = os.listdir(tmp_extract)
            missing = content_writer.verify_top_level_entries(mount_path, top_level)
            if missing:
                raise ContentError(f"verification failed — missing after copy: {missing}")

            status(f"Unmounting {partition_path}…")
            formatter.unmount_partition(partition_path)
            mount_path = None

            GLib.idle_add(self._progress_view.show_done, "Done. It is now safe to physically remove the card.")
        except (FormatError, SafetyError, ContentError) as exc:
            log.exception("prepare flow failed")
            GLib.idle_add(self._progress_view.show_error, str(exc))
        except Exception as exc:  # noqa: BLE001 - last resort so the GUI never shows a raw traceback
            log.exception("unexpected error in prepare flow")
            GLib.idle_add(self._progress_view.show_error, f"Unexpected error: {exc}")
        finally:
            content_writer.cleanup_temp_dir(tmp_extract)
            if mount_path and partition_path:
                try:
                    formatter.unmount_partition(partition_path)
                except FormatError:
                    pass
            GLib.idle_add(self._start_over_button.set_visible, True)
