"""Progress display: a status page with one row per phase, where extraction
and copy each show their own progress bar (§4.4), a collapsible log, and the
final done/error state.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from gui.icon import app_icon

# (key, title) in the order the worker runs them.
PHASES = (
    ("format", "Format as FAT32"),
    ("extract", "Extract zip"),
    ("mount", "Mount"),
    ("copy", "Copy files"),
    ("finish", "Verify and unmount"),
)


class _PhaseRow(Adw.ActionRow):
    def __init__(self, title: str) -> None:
        super().__init__(title=title)
        self._icon = Gtk.Image(pixel_size=16)
        self._spinner = Gtk.Spinner()
        self.add_prefix(self._icon)
        self.add_prefix(self._spinner)
        self._bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=160, visible=False)
        self.add_suffix(self._bar)
        self.set_state("pending")

    def set_state(self, state: str) -> None:
        active = state == "active"
        self._spinner.set_visible(active)
        self._spinner.set_spinning(active)
        self._icon.set_visible(not active)
        for css in ("success", "error", "dim-label"):
            self._icon.remove_css_class(css)
        if state == "done":
            self._icon.set_from_icon_name("object-select-symbolic")
            self._icon.add_css_class("success")
            self._bar.set_visible(False)
        elif state == "failed":
            self._icon.set_from_icon_name("dialog-error-symbolic")
            self._icon.add_css_class("error")
        elif state == "pending":
            self._icon.set_from_icon_name("content-loading-symbolic")
            self._icon.add_css_class("dim-label")
            self._bar.set_visible(False)

    def set_fraction(self, fraction: float) -> None:
        self._bar.set_visible(True)
        self._bar.set_fraction(max(0.0, min(fraction, 1.0)))


class ProgressView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.CENTER)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, f"set_margin_{side}")(24)
        clamp = Adw.Clamp(maximum_size=480, child=content)
        self.append(Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))

        self._icon_slot = Gtk.Box(halign=Gtk.Align.CENTER)
        content.append(self._icon_slot)

        self._title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._title.add_css_class("title-1")
        self._description = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._description.add_css_class("dim-label")
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading.append(self._title)
        heading.append(self._description)
        content.append(heading)

        phase_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        phase_list.add_css_class("boxed-list")
        self._rows: dict[str, _PhaseRow] = {}
        for key, title in PHASES:
            row = _PhaseRow(title)
            phase_list.append(row)
            self._rows[key] = row
        content.append(phase_list)

        self._log_buffer = Gtk.TextBuffer()
        log_view = Gtk.TextView(buffer=self._log_buffer, editable=False, monospace=True, cursor_visible=False)
        log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_view.add_css_class("card")
        for side in ("top", "bottom", "left", "right"):
            getattr(log_view, f"set_{side}_margin")(8)
        scroller = Gtk.ScrolledWindow(child=log_view, min_content_height=140)
        self._expander = Gtk.Expander(label="Details", child=scroller)
        content.append(self._expander)

        self.button = Gtk.Button(halign=Gtk.Align.CENTER, visible=False)
        self.button.add_css_class("pill")
        content.append(self.button)

        self._active: str | None = None
        self.reset()

    def reset(self) -> None:
        self._set_icon(app_icon(96))
        self._title.set_label("Preparing your card…")
        self._description.set_label("Don't remove the card until Stamp is done.")
        for row in self._rows.values():
            row.set_state("pending")
        self._active = None
        self._log_buffer.set_text("")
        self._expander.set_expanded(False)
        self.button.set_visible(False)
        self.button.remove_css_class("suggested-action")

    def _set_icon(self, image: Gtk.Image) -> None:
        child = self._icon_slot.get_first_child()
        if child is not None:
            self._icon_slot.remove(child)
        self._icon_slot.append(image)

    def set_phase(self, key: str) -> None:
        """Mark `key` active and every phase before it done."""
        keys = [k for k, _ in PHASES]
        for earlier in keys[: keys.index(key)]:
            self._rows[earlier].set_state("done")
        self._rows[key].set_state("active")
        self._active = key

    def set_status(self, text: str) -> None:
        self._description.set_label(text)

    def log(self, text: str) -> None:
        end = self._log_buffer.get_end_iter()
        self._log_buffer.insert(end, text.rstrip("\n") + "\n")

    def set_phase_fraction(self, phase: str, fraction: float) -> None:
        row = self._rows.get(phase)
        if row is not None:
            row.set_fraction(fraction)

    def show_done(self, message: str) -> None:
        for row in self._rows.values():
            row.set_state("done")
        self._set_icon(self._status_icon("object-select-symbolic", "success"))
        self._title.set_label("Your card is ready")
        self._description.set_label(message)
        self.button.set_label("Prepare Another Card")
        self.button.add_css_class("suggested-action")
        self.button.set_visible(True)

    def show_error(self, message: str) -> None:
        if self._active is not None:
            self._rows[self._active].set_state("failed")
        self._set_icon(self._status_icon("dialog-error-symbolic", "error"))
        self._title.set_label("Something went wrong")
        self._description.set_label(message)
        self._expander.set_expanded(True)
        self.button.set_label("Back")
        self.button.set_visible(True)

    @staticmethod
    def _status_icon(name: str, css: str) -> Gtk.Image:
        image = Gtk.Image(icon_name=name, pixel_size=96)
        image.add_css_class(css)
        return image
