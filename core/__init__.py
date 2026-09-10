"""Core logic for Stamp: device scanning, formatting, content writing, safety checks.

This package must never import GTK/GObject GUI types (see ARCHITECTURE.md).
It only talks to the system via subprocess (lsblk, findmnt, rsync, unzip) and,
in formatter.py, via udisks2 D-Bus / a privileged helper invoked through pkexec.
That boundary is what lets a non-GTK GUI (Qt, web) reuse this code later.
"""
