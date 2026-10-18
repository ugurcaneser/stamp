"""A concise destructive-action confirmation dialog (§4.5).

The selected device and archive are summarized in one place. Removable drives
can proceed immediately; devices not flagged removable need one risk
acknowledgement in the same dialog. No destructive action can occur before the
"Erase & Copy" response is pressed.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from core.content_writer import ZipPreview
from core.formatter import sanitize_label
from core.models import Device, human_size


class ConfirmDialog(Adw.MessageDialog):
    """The single "Erase & Copy" gate for destructive work."""

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
            "Everything on this card will be deleted.",
            "",
            f"Card: {device.display_model}  ·  {device.size_human}  ·  {device.path}",
            f"Zip: {os.path.basename(zip_path)}  ·  "
            f"{preview.file_count} files  ·  {human_size(preview.uncompressed_size_bytes)}",
            f"Name: {sanitize_label(label)}",
        ]

        super().__init__(
            transient_for=parent,
            modal=True,
            heading=f"Erase {device.display_model}?",
            body="\n".join(body_lines),
        )

        self._on_result = on_result

        self.add_response("cancel", "Cancel")
        self.add_response("format", "Erase & Copy")
        self.set_response_appearance("format", Adw.ResponseAppearance.DESTRUCTIVE)
        self.set_default_response("cancel")
        self.set_close_response("cancel")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(8)

        self._non_removable_checkbox: Gtk.CheckButton | None = None
        if not device.removable:
            self.set_response_enabled("format", False)
            self._non_removable_checkbox = Gtk.CheckButton(
                label="Use this device even though it is not marked removable"
            )
            self._non_removable_checkbox.connect("toggled", self._update_can_confirm)
            content.append(self._non_removable_checkbox)

        self.set_extra_child(content)
        self.connect("response", self._on_response)

    def _update_can_confirm(self, *_args) -> None:
        assert self._non_removable_checkbox is not None
        self.set_response_enabled("format", self._non_removable_checkbox.get_active())

    def _on_response(self, _dialog: Adw.MessageDialog, response: str) -> None:
        self._on_result(response == "format")
