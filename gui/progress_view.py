"""Progress display: format status, two visible phase progress bars
(extraction, copy) per §4.4, a scrolling log, and a completion/error banner.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class ProgressView(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._banner = Adw.Banner(title="", revealed=False)
        self.append(self._banner)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._spinner = Gtk.Spinner()
        status_row.append(self._spinner)
        self._status_label = Gtk.Label(label="Preparing...", xalign=0.0)
        self._status_label.add_css_class("heading")
        status_row.append(self._status_label)
        self.append(status_row)

        self.append(Gtk.Label(label="Extracting zip", xalign=0.0))
        self._extract_bar = Gtk.ProgressBar(show_text=True)
        self.append(self._extract_bar)

        self.append(Gtk.Label(label="Copying to card", xalign=0.0))
        self._copy_bar = Gtk.ProgressBar(show_text=True)
        self.append(self._copy_bar)

        self._log_buffer = Gtk.TextBuffer()
        log_view = Gtk.TextView(buffer=self._log_buffer, editable=False, monospace=True)
        log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(log_view)
        scroller.set_vexpand(True)
        scroller.set_min_content_height(160)
        self.append(scroller)

        self._spinner.start()

    def reset(self) -> None:
        self._status_label.set_text("Preparing...")
        self._extract_bar.set_fraction(0.0)
        self._extract_bar.set_text("0%")
        self._copy_bar.set_fraction(0.0)
        self._copy_bar.set_text("0%")
        self._log_buffer.set_text("")
        self._banner.set_revealed(False)
        self._spinner.start()

    def set_status(self, text: str) -> None:
        self._status_label.set_text(text)

    def log(self, text: str) -> None:
        end = self._log_buffer.get_end_iter()
        self._log_buffer.insert(end, text.rstrip("\n") + "\n")

    def set_phase_fraction(self, phase: str, fraction: float) -> None:
        bar = self._extract_bar if phase == "extract" else self._copy_bar if phase == "copy" else None
        if bar is None:
            return
        bar.set_fraction(max(0.0, min(fraction, 1.0)))
        bar.set_text(f"{int(fraction * 100)}%")

    def show_done(self, message: str) -> None:
        self._spinner.stop()
        self.set_status("Done")
        self._banner.set_title(message)
        self._banner.remove_css_class("error")
        self._banner.set_revealed(True)

    def show_error(self, message: str) -> None:
        self._spinner.stop()
        self.set_status("Failed")
        self._banner.set_title(message)
        self._banner.add_css_class("error")
        self._banner.set_revealed(True)
