"""The app icon, from the icon theme when installed or from assets/ when run
from a source checkout (the .deb installs it under the app ID's name).
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

APP_ID = "com.ugurcaneser.Stamp"

_SOURCE_ICON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "stamp-icon.svg")


def app_icon(pixel_size: int) -> Gtk.Image:
    display = Gdk.Display.get_default()
    if display is not None and Gtk.IconTheme.get_for_display(display).has_icon(APP_ID):
        image = Gtk.Image.new_from_icon_name(APP_ID)
    elif os.path.exists(_SOURCE_ICON):
        image = Gtk.Image.new_from_file(_SOURCE_ICON)
    else:
        image = Gtk.Image.new_from_icon_name("drive-removable-media")
    image.set_pixel_size(pixel_size)
    return image
