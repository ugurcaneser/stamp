"""Adw.Application entry point for the GUI."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from core import logging_setup

from gui.main_window import MainWindow

APP_ID = "org.example.Stamp"


class StampApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._window: MainWindow | None = None

    def do_activate(self) -> None:  # noqa: D102 - GObject virtual method override
        if self._window is None:
            self._window = MainWindow(self)
        self._window.present()


def main() -> int:
    logging_setup.configure_logging()
    app = StampApplication()
    return app.run(None)


if __name__ == "__main__":
    raise SystemExit(main())
