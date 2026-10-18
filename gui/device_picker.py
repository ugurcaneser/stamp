"""Device list group: shows candidate removable devices, auto-refreshes,
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


class DevicePicker(Adw.PreferencesGroup):
    """Step 1 of the select page: one radio row per candidate device, with a
    refresh button in the group header and a single-row empty state."""

    __gsignals__ = {
        # Emitted whenever the user picks a different device (or None, if the
        # previous selection disappeared, e.g. card physically removed).
        "device-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, title: str) -> None:
        super().__init__(title=title)

        self._devices: list[Device] = []
        self._selected_path: str | None = None
        self._root_disk_path: str | None = None
        self._poller: DevicePoller | None = None
        self._rows: dict[str, Adw.ActionRow] = {}

        refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER)
        refresh_button.add_css_class("flat")
        refresh_button.set_tooltip_text("Refresh")
        refresh_button.connect("clicked", lambda *_: self.refresh())
        self.set_header_suffix(refresh_button)

        self._empty_row = Adw.ActionRow(
            title="No card found",
            subtitle="Insert an SD card or USB drive. It shows up here automatically.",
        )
        self._empty_row.add_prefix(Gtk.Image(icon_name="media-removable-symbolic", pixel_size=24))
        self._empty_row.add_css_class("dim-label")
        self.add(self._empty_row)

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
            self.remove(row)
        self._rows.clear()

        self._empty_row.set_title("No card found")
        self._empty_row.set_subtitle("Insert an SD card or USB drive. It shows up here automatically.")
        self._empty_row.set_visible(len(candidates) == 0)

        # Settle the selection before building rows so the right radio starts checked.
        selection_lost = self._selected_path not in {d.path for d in candidates}
        if selection_lost:
            self._selected_path = candidates[0].path if candidates else None

        for device in candidates:
            row = self._build_row(device)
            self.add(row)
            self._rows[device.path] = row

        if selection_lost:
            self._select(self._selected_path)

        return False  # GLib.idle_add: run once

    def _show_scan_error(self, exc: Exception) -> bool:
        self._empty_row.set_title("Could not list devices")
        self._empty_row.set_subtitle(str(exc))
        self._empty_row.set_visible(True)
        return False

    def _build_row(self, device: Device) -> Adw.ActionRow:
        subtitle_bits = [device.path]
        if device.transport:
            subtitle_bits.append(device.transport.upper())
        if not device.removable:
            subtitle_bits.append("not marked removable")
        row = Adw.ActionRow(
            title=f"{device.display_model}  ·  {device.size_human}",
            subtitle="  ·  ".join(subtitle_bits),
        )

        check = Gtk.CheckButton()
        check.set_group(self._radio_group_anchor())
        check.set_active(device.path == self._selected_path)
        check.connect("toggled", self._on_row_toggled, device.path)
        row.add_prefix(check)
        row.set_activatable_widget(check)

        if not device.removable:
            warn_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warn_icon.set_tooltip_text("Not marked as removable")
            warn_icon.add_css_class("warning")
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
