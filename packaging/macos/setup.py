"""py2app build script for a genuinely standalone Unreal Custodian.app --
Python + Tk bundled inside, no requirement that the end user has Python
installed at all. Build with:

    cd packaging/macos
    .build-venv/bin/python setup.py py2app

Distinct from the thin shell-script wrapper also named "Unreal Custodian.app"
in this directory (that one execs whatever Python the user already has and
exists for people running from a cloned checkout). This one is what gets
attached to GitHub releases.
"""
import sys
from pathlib import Path

from setuptools import setup

# custodian/ lives at the repo root, two directories up from this file --
# py2app's module discovery (modulegraph) walks sys.path like a normal import.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

APP = ["launch_gui.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "icon.icns",
    "packages": ["custodian"],
    "includes": ["tkinter"],
    "plist": {
        "CFBundleName": "Unreal Custodian",
        "CFBundleDisplayName": "Unreal Custodian",
        "CFBundleIdentifier": "com.alexcoulombepresents.unreal-custodian",
        "CFBundleShortVersionString": "0.3.1",
        "CFBundleVersion": "0.3.1",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.developer-tools",
    },
}

setup(
    name="Unreal Custodian",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
