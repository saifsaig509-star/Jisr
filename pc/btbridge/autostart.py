"""Start the program automatically when the user logs in to Windows (HKCU\\...\\Run, no admin needed)."""
import os
import sys

IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Jisr"
LEGACY_NAMES = ("BTBridge",)          # the entry written before the program was renamed

if IS_WIN:
    import winreg


def supported() -> bool:
    return IS_WIN


def command() -> str:
    """The command line Windows runs at login (always starts hidden in the tray)."""
    if getattr(sys, "frozen", False):                      # the packaged Jisr.exe
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # pythonw = no console window
    if os.path.exists(pyw):
        exe = pyw
    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "btbridge_gui.pyw")
    return f'"{exe}" "{launcher}" --minimized'


def _has(name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
        return True
    except OSError:
        return False


def is_enabled() -> bool:
    return IS_WIN and _has(VALUE_NAME)


def migrate() -> None:
    """The program used to start with Windows under another name (and another exe): re-register it under the
    new name, pointing at the program that is running now, and drop the old entry."""
    if IS_WIN and not _has(VALUE_NAME) and any(_has(n) for n in LEGACY_NAMES):
        set_enabled(True)


def set_enabled(on: bool) -> bool:
    """Returns True if the registry now matches `on`."""
    if not IS_WIN:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command())
            for name in (LEGACY_NAMES if on else (VALUE_NAME,) + LEGACY_NAMES):
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
