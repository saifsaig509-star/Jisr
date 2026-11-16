"""Windows Explorer: right-click a file > Send to > Jisr.

This is a shortcut (.lnk) in the user's SendTo folder that starts  Jisr --send <files>.  A shortcut,
not a .cmd file, so file names in any language (Arabic, Kurdish ...) reach the program unchanged.
"""
import os
import subprocess
import sys

IS_WIN = sys.platform == "win32"
NAME = "Jisr.lnk"
LEGACY_NAMES = ("BT Bridge.lnk",)     # the shortcut written before the program was renamed

_SCRIPT = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BTB_LNK);"
           "$s.TargetPath = $env:BTB_TARGET; $s.Arguments = $env:BTB_ARGS;"
           "$s.Description = 'Send to the phone with Jisr'; $s.Save()")


def _folder() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "SendTo")


def link_path() -> str:
    return os.path.join(_folder(), NAME)


def supported() -> bool:
    return IS_WIN and bool(os.environ.get("APPDATA"))


def target_and_args():
    """What the shortcut runs: (program, arguments). Same program the autostart entry uses."""
    if getattr(sys, "frozen", False):                      # the packaged Jisr.exe
        return sys.executable, "--send"
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # pythonw = no console window
    if os.path.exists(pyw):
        exe = pyw
    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "btbridge_gui.pyw")
    return exe, f'"{launcher}" --send'


def is_enabled() -> bool:
    return supported() and os.path.exists(link_path())


def migrate() -> None:
    """Replace the shortcut of the old name by one that starts the program that is running now."""
    if supported() and not is_enabled() and any(os.path.exists(os.path.join(_folder(), n)) for n in LEGACY_NAMES):
        set_enabled(True)


def set_enabled(on: bool) -> bool:
    """Returns True when the Send to menu now matches `on`."""
    if not supported():
        return False
    try:
        for name in LEGACY_NAMES + ((NAME,) if not on else ()):
            old = os.path.join(_folder(), name)
            if os.path.exists(old):
                os.remove(old)
        if not on:
            return True
        target, args = target_and_args()
        env = dict(os.environ, BTB_LNK=link_path(), BTB_TARGET=target, BTB_ARGS=args)
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
                       env=env, check=True, timeout=30, creationflags=0x08000000)   # no console window
        return os.path.exists(link_path())
    except (OSError, subprocess.SubprocessError):
        return False
