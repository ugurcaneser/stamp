"""Two-step destructive confirmation (§4.5).

1. If the selected device isn't flagged removable, a dedicated warning dialog
   must be explicitly acknowledged first (a distinct step, not just a label).
2. The main confirm dialog requires BOTH the "I understand this erases
   everything" checkbox AND typing the exact device path before the
   "Format & Copy" button becomes clickable. No destructive action can occur
   before that button is pressed.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from core import safety
from core.content_writer import ZipPreview
from core.models import Device, human_size


def confirm_non_removable_override(
    parent: Gtk.Window,
    device: Device,
    on_result: Callable[[bool], None],
) -> None:
    dialog = Adw.MessageDialog(
        transient_for=parent,
        modal=True,
        heading="This device is not flagged as removable",
        body=(
            f"{device.path} ({device.size_human}, {device.display_model}) was not reported as a "
            "removable drive by the system. That usually means it's an internal disk, not an SD "
            "card or USB drive.\n\n"
            "Only continue if you are certain this is the correct, intended device."
        ),
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("override", "I understand the risk, continue")
    dialog.set_response_appearance("override", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def _on_response(_dialog: Adw.MessageDialog, response: str) -> None:
        on_result(response == "override")

    dialog.connect("response", _on_response)
    dialog.present()


class ConfirmDialog(Adw.MessageDialog):
    """The final "Format & Copy" gate. Nothing destructive happens until this
    dialog's format response fires, and it can't fire until both confirmation
    mechanisms are satisfied.
    """

    def __init__(
        self,
        parent: Gtk.Window,
        device: Device,
        zip_path: str,
        preview: ZipPreview,
        label: str,
        on_result: Callable[[bool], None],
        non_removable_override_already_granted: bool = False,
    ) -> None:
        body_lines = [
            f"Device:  {device.path}  ({device.size_human}, {device.display_model})",
            f"Zip:     {os.path.basename(zip_path)}  "
            f"({preview.file_count} files, {human_size(preview.uncompressed_size_bytes)})",
            f"Label:   {label or 'STAMP'}",
            "",
            "Everything currently on this device will be permanently erased and replaced "
            "with the zip's contents. This cannot be undone.",
        ]
        for w in preview.warnings:
            body_lines.insert(-2, f"⚠ {w}")
        if non_removable_override_already_granted:
            body_lines.insert(0, "⚠ Proceeding on a device NOT flagged as removable.\n")

        super().__init__(
            transient_for=parent,
            modal=True,
            heading="Format & erase this device?",
            body="\n".join(body_lines),
        )

        self._device = device
        self._on_result = on_result

        self.add_response("cancel", "Cancel")
        self.add_response("format", "Format & Copy")
        self.set_response_appearance("format", Adw.ResponseAppearance.DESTRUCTIVE)
        self.set_default_response("cancel")
        self.set_close_response("cancel")
        self.set_response_enabled("format", False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(8)

        self._checkbox = Gtk.CheckButton(label=safety.CONFIRM_CHECKBOX_TEXT)
        self._checkbox.connect("toggled", self._update_can_confirm)
        content.append(self._checkbox)

        entry_label = Gtk.Label(label=f"Type the device path to confirm ({device.path}):", xalign=0.0)
        content.append(entry_label)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text(device.path)
        self._entry.connect("changed", self._update_can_confirm)
        content.append(self._entry)

        self.set_extra_child(content)
        self.connect("response", self._on_response)

    def _update_can_confirm(self, *_args) -> None:
        matches = safety.confirmation_text_matches(self._device.path, self._entry.get_text())
        checked = safety.checkbox_confirms(self._checkbox.get_active())
        self.set_response_enabled("format", matches and checked)

    def _on_response(self, _dialog: Adw.MessageDialog, response: str) -> None:
        self._on_result(response == "format")
