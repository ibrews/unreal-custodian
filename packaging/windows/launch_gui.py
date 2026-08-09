"""PyInstaller entry point. Same role as packaging/macos/launch_gui.py --
kept separate from custodian/gui.py's own __main__ block so the package
itself has no packaging-tool-specific code in it.
"""
from custodian.gui import main

if __name__ == "__main__":
    main()
