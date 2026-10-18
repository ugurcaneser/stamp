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
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from core import content_writer, formatter, logging_setup, safety
from core.content_writer import ContentError, ZipPreview
from core.formatter import DEFAULT_LABEL, FormatError, sanitize_label
from core.models import Device, human_size
from core.safety import SafetyError

from gui.confirm_dialog import ConfirmDialog
from gui.device_picker import DevicePicker
from gui.icon import app_icon
from gui.progress_view import ProgressView

log = logging_setup.get_logger()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Stamp", default_width=600, default_height=720)

        self._zip_path: str | None = None
        self._zip_preview: ZipPreview | None = None
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
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{side}")(24)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        header.append(app_icon(56))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        title = Gtk.Label(label="Stamp", xalign=0.0)
        title.add_css_class("title-1")
        subtitle = Gtk.Label(label="Erase a card as FAT32 and copy a zip's contents onto it.", xalign=0.0, wrap=True)
        subtitle.add_css_class("dim-label")
        titles.append(title)
        titles.append(subtitle)
        header.append(titles)
        content.append(header)

        self._error_banner = Adw.Banner(title="", revealed=False)
        self._error_banner.add_css_class("error")
        content.append(self._error_banner)

        self._device_picker = DevicePicker(title="1  Choose a card")
        self._device_picker.connect("device-selected", self._on_device_selected)
        content.append(self._device_picker)

        zip_group = Adw.PreferencesGroup(title="2  Choose a zip file")
        self._zip_row = Adw.ActionRow()
        self._zip_row.add_prefix(Gtk.Image(icon_name="package-x-generic-symbolic", pixel_size=24))
        self._choose_button = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        self._choose_button.connect("clicked", self._on_choose_zip_clicked)
        self._zip_row.add_suffix(self._choose_button)
        self._reset_zip_row()
        zip_group.add(self._zip_row)
        content.append(zip_group)

        self._label_group = Adw.PreferencesGroup(title="3  Name the card")
        self._label_row = Adw.EntryRow(title="Card name")
        self._label_row.set_text(DEFAULT_LABEL)
        self._label_row.connect("changed", lambda *_: self._update_label_hint())
        self._label_group.add(self._label_row)
        content.append(self._label_group)
        self._update_label_hint()

        clamp = Adw.Clamp(maximum_size=560, child=content)
        scroller = Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        # Dropping a .zip anywhere on the page picks it.
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_zip_dropped)
        scroller.add_controller(drop)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        for side in ("top", "bottom", "start", "end"):
            getattr(footer, f"set_margin_{side}")(16)
        self._hint_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        self._hint_label.add_css_class("dim-label")
        footer.append(self._hint_label)
        self._format_button = Gtk.Button(label="Format & Copy…")
        self._format_button.add_css_class("suggested-action")
        self._format_button.add_css_class("pill")
        self._format_button.connect("clicked", self._on_format_clicked)
        footer.append(self._format_button)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(scroller)
        page.append(Gtk.Separator())
        page.append(footer)
        self._stack.add_named(page, "select")

        self._update_format_button_sensitivity()
        self._device_picker.start_auto_refresh()

    def _reset_zip_row(self) -> None:
        self._zip_row.set_title("No zip file selected")
        self._zip_row.set_subtitle("Drop a zip file here or choose one")
        self._choose_button.set_label("Choose…")

    def _update_label_hint(self) -> None:
        text = self._label_row.get_text()
        written = sanitize_label(text)
        if written != text:
            self._label_group.set_description(f"Will be written as {written}")
        else:
            self._label_group.set_description("Shown as the card's name. Up to 11 letters or digits.")

    def _on_zip_dropped(self, _target: Gtk.DropTarget, gfile: Gio.File, _x: float, _y: float) -> bool:
        path = gfile.get_path()
        if not path or not path.lower().endswith(".zip"):
            return False
        self._load_zip(path)
        return True

    def _on_device_selected(self, _picker: DevicePicker, device: Device | None) -> None:
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
            self._reset_zip_row()
            self._update_format_button_sensitivity()
            return

        self._error_banner.set_revealed(False)
        self._zip_path = path
        self._zip_preview = preview
        self._zip_row.set_title(os.path.basename(path))
        subtitle = f"{preview.file_count} files  ·  {human_size(preview.uncompressed_size_bytes)}"
        self._zip_row.set_subtitle(subtitle)
        self._choose_button.set_label("Change…")

        if preview.warnings:
            self._error_banner.set_title(" ".join(preview.warnings))
            self._error_banner.set_revealed(True)

        self._update_format_button_sensitivity()

    def _update_format_button_sensitivity(self) -> None:
        device = self._device_picker.selected_device
        self._format_button.set_sensitive(device is not None and self._zip_path is not None)
        if device is None:
            hint = "Insert a card to continue."
        elif self._zip_path is None:
            hint = "Choose a zip file to continue."
        else:
            hint = "Everything on the card will be erased."
        self._hint_label.set_label(hint)

    def _on_format_clicked(self, _button: Gtk.Button) -> None:
        device = self._device_picker.selected_device
        if device is None or self._zip_path is None or self._zip_preview is None:
            return

        root_disk = self._device_picker.root_disk_path or ""
        allow_override = not device.removable
        check = safety.evaluate_device_safety(
            device, root_disk, allow_non_removable_override=allow_override
        )
        if not check.ok:
            self._error_banner.set_title("; ".join(check.blocking_reasons))
            self._error_banner.set_revealed(True)
            return

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
        )
        dialog.present()

    def _on_confirm_result(self, confirmed: bool, device: Device) -> None:
        if not confirmed:
            return
        self._start_prepare_flow(device, allow_override=not device.removable)

    # --- progress page + background worker --------------------------------------------

    def _build_progress_page(self) -> None:
        self._progress_view = ProgressView()
        self._progress_view.button.connect("clicked", lambda *_: self._return_to_select_page())
        self._stack.add_named(self._progress_view, "progress")

    def _return_to_select_page(self) -> None:
        self._stack.set_visible_child_name("select")
        self._device_picker.refresh()

    def _start_prepare_flow(self, device: Device, allow_override: bool) -> None:
        assert self._zip_path is not None
        zip_path = self._zip_path
        label = self._label_row.get_text()
        root_disk = self._device_picker.root_disk_path or ""
        self._progress_view.reset()
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

        def phase(key: str) -> None:
            GLib.idle_add(self._progress_view.set_phase, key)

        def phase_progress(phase: str, fraction: float) -> None:
            GLib.idle_add(self._progress_view.set_phase_fraction, phase, fraction)

        tmp_extract = tempfile.mkdtemp(prefix="stamp-extract-")
        mount_path: str | None = None
        partition_path: str | None = None
        try:
            phase("format")
            result = formatter.format_device(
                device,
                root_disk,
                label=label,
                allow_non_removable_override=allow_override,
                progress_cb=status,
            )
            partition_path = result.partition_path

            phase("extract")
            status("Extracting zip…")
            content_writer.extract_zip(zip_path, tmp_extract, progress_cb=phase_progress)

            phase("mount")
            status(f"Mounting {partition_path}…")
            mount_path = formatter.mount_partition(partition_path, progress=status)

            phase("copy")
            status("Copying files onto the card…")
            content_writer.copy_tree_rsync(tmp_extract, mount_path, progress_cb=phase_progress)

            phase("finish")
            status("Flushing writes to the card…")
            content_writer.sync_and_flush()

            top_level = os.listdir(tmp_extract)
            missing = content_writer.verify_top_level_entries(mount_path, top_level)
            if missing:
                raise ContentError(f"verification failed — missing after copy: {missing}")

            status(f"Unmounting {partition_path}…")
            formatter.unmount_partition(partition_path)
            mount_path = None

            GLib.idle_add(self._progress_view.show_done, "It's unmounted and safe to remove.")
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
