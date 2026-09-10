"""GTK4 + libadwaita GUI. Everything destructive happens through core/ — this
package only builds widgets, marshals background-thread progress back to the
main loop via GLib.idle_add, and enforces the safety UX flow.
"""
