"""py2app entry point. Kept separate from custodian/gui.py's own __main__ block
so the package itself has no py2app-specific code in it -- this file only
exists for the bundled build.
"""
from custodian.gui import main

if __name__ == "__main__":
    main()
