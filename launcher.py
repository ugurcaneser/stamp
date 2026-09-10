"""Single entry point installed as `/usr/bin/stamp`.

`stamp` with no arguments launches the GTK4/libadwaita GUI.
`stamp --cli ...` runs the scriptable CLI mode (§5), sharing core/ with the GUI.
"""

from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--cli":
        from cli.main import main as cli_main

        return cli_main(argv[1:])

    from gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
