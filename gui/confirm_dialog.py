"""A concise destructive-action confirmation dialog (§4.5).

The selected device and archive are summarized in one place. Removable drives
need one erase acknowledgement; devices not flagged removable need one extra
risk acknowledgement in the same dialog. No destructive action can occur
before the enabled "Format & Copy" response is pressed.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from core.content_writer import ZipPreview
from core.models import Device, human_size


class ConfirmDialog(Adw.MessageDialog):
    """The single "Format & Copy" gate for destructive work."""

    def __init__(
        self,
        parent: Gtk.Window,
        device: Device,
        zip_path: str,
        preview: ZipPreview,
        label: str,
        on_result: Callable[[bool], None],
    ) -> None:
        body_lines = [
            f"{device.path}  ·  {device.size_human}  ·  {device.display_model}",
            f"{os.path.basename(zip_path)}  ·  "
            f"{preview.file_count} files  ·  {human_size(preview.uncompressed_size_bytes)}",
            f"FAT32 label: {label or 'STAMP'}",
        ]

        super().__init__(
            transient_for=parent,
            modal=True,
            heading=f"Format {device.path}?",
            body="\n".join(body_lines),
        )

        self._on_result = on_result

        self.add_response("cancel", "Cancel")
        self.add_response("format", "Format & Copy")
        self.set_response_appearance("format", Adw.ResponseAppearance.DESTRUCTIVE)
        self.set_default_response("cancel")
        self.set_close_response("cancel")
        self.set_response_enabled("format", False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(8)

        self._checkbox = Gtk.CheckButton(label=f"Erase all data on {device.path}")
        self._checkbox.connect("toggled", self._update_can_confirm)
        content.append(self._checkbox)

        self._non_removable_checkbox: Gtk.CheckButton | None = None
        if not device.removable:
            self._non_removable_checkbox = Gtk.CheckButton(
                label="Use this device even though it is not marked removable"
            )
            self._non_removable_checkbox.connect("toggled", self._update_can_confirm)
            content.append(self._non_removable_checkbox)

        self.set_extra_child(content)
        self.connect("response", self._on_response)

    def _update_can_confirm(self, *_args) -> None:
        erase_confirmed = self._checkbox.get_active()
        device_confirmed = (
            self._non_removable_checkbox is None
            or self._non_removable_checkbox.get_active()
        )
        self.set_response_enabled("format", erase_confirmed and device_confirmed)

    def _on_response(self, _dialog: Adw.MessageDialog, response: str) -> None:
        self._on_result(response == "format")
