"""Device list widget: shows candidate removable devices, auto-refreshes,
and reports the current selection via a GObject signal.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from core import safety
from core.device_scanner import DevicePoller, DeviceScanError, scan_devices
from core.models import Device


class DevicePicker(Gtk.Box):
    """A Gtk.Box wrapping an Adw.PreferencesGroup of device rows + a refresh button."""

    __gsignals__ = {
        # Emitted whenever the user picks a different device (or None, if the
        # previous selection disappeared, e.g. card physically removed).
        "device-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self._devices: list[Device] = []
        self._selected_path: str | None = None
        self._root_disk_path: str | None = None
        self._poller: DevicePoller | None = None
        self._rows: dict[str, Adw.ActionRow] = {}

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Target device", xalign=0.0)
        title.add_css_class("heading")
        header.append(title)
        header.set_hexpand(True)

        spacer = Gtk.Box(hexpand=True)
        header.append(spacer)

        self._refresh_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_button.set_tooltip_text("Refresh device list")
        self._refresh_button.connect("clicked", lambda *_: self.refresh())
        header.append(self._refresh_button)
        self.append(header)

        self._group = Adw.PreferencesGroup()
        self.append(self._group)

        self._empty_status = Adw.StatusPage(
            icon_name="drive-removable-media-symbolic",
            title="No removable devices found",
            description="Insert an SD card or USB drive, then press refresh.",
        )
        self._empty_status.set_visible(False)
        self.append(self._empty_status)

    # --- lifecycle -----------------------------------------------------------

    def start_auto_refresh(self, interval_seconds: float = 2.0) -> None:
        if self._poller is not None:
            return
        self.refresh()
        self._poller = DevicePoller(
            on_change=lambda devices: GLib.idle_add(self._apply_devices, devices),
            on_error=lambda exc: GLib.idle_add(self._show_scan_error, exc),
            interval_seconds=interval_seconds,
        )
        self._poller.start()

    def stop_auto_refresh(self) -> None:
        if self._poller is not None:
            self._poller.stop()
            self._poller = None

    # --- data ------------------------------------------------------------------

    def refresh(self) -> None:
        try:
            devices = scan_devices()
            self._root_disk_path = safety.get_root_disk_path()
        except (DeviceScanError, safety.SafetyError) as exc:
            self._show_scan_error(exc)
            return
        self._apply_devices(devices)

    def _apply_devices(self, devices: list[Device]) -> bool:
        candidates = (
            safety.filter_candidate_devices(devices, self._root_disk_path)
            if self._root_disk_path
            else devices
        )
        self._devices = candidates

        # rebuild rows
        for row in list(self._rows.values()):
            self._group.remove(row)
        self._rows.clear()

        self._empty_status.set_visible(len(candidates) == 0)

        for device in candidates:
            row = self._build_row(device)
            self._group.add(row)
            self._rows[device.path] = row

        if self._selected_path not in self._rows:
            self._select(candidates[0].path if candidates else None)

        return False  # GLib.idle_add: run once

    def _show_scan_error(self, exc: Exception) -> bool:
        self._empty_status.set_title("Could not list devices")
        self._empty_status.set_description(str(exc))
        self._empty_status.set_visible(True)
        return False

    def _build_row(self, device: Device) -> Adw.ActionRow:
        subtitle_bits = [device.display_model]
        if not device.removable:
            subtitle_bits.append("not flagged removable")
        if device.all_mountpoints:
            subtitle_bits.append("mounted: " + ", ".join(device.all_mountpoints))
        row = Adw.ActionRow(title=f"{device.path}  ·  {device.size_human}", subtitle=" — ".join(subtitle_bits))

        check = Gtk.CheckButton()
        check.set_group(self._radio_group_anchor())
        check.set_active(device.path == self._selected_path)
        check.connect("toggled", self._on_row_toggled, device.path)
        row.add_prefix(check)
        row.set_activatable_widget(check)

        if not device.removable:
            warn_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warn_icon.set_tooltip_text("Not marked as removable")
            row.add_suffix(warn_icon)

        return row

    _anchor_check: Gtk.CheckButton | None = None

    def _radio_group_anchor(self):
        if self._anchor_check is None:
            self._anchor_check = Gtk.CheckButton()
        return self._anchor_check

    def _on_row_toggled(self, check: Gtk.CheckButton, path: str) -> None:
        if check.get_active():
            self._select(path)

    def _select(self, path: str | None) -> None:
        self._selected_path = path
        self.emit("device-selected", self.selected_device)

    @property
    def selected_device(self) -> Device | None:
        return next((d for d in self._devices if d.path == self._selected_path), None)

    @property
    def root_disk_path(self) -> str | None:
        return self._root_disk_path
