"""Text clipboard on Windows (ctypes, no dependencies). Elsewhere it reports `available = False`.

The backend is replaceable so the rest of the program can be tested on any system.
"""
import sys
import time

IS_WIN = sys.platform == "win32"
MAX_CHARS = 200_000          # longer text is refused (a frame may not exceed 1 MB)
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class WinBackend:
    """Raw access to the Windows clipboard. Every call opens and closes the clipboard by itself."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        u, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
        u.OpenClipboard.argtypes, u.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
        u.CloseClipboard.argtypes, u.CloseClipboard.restype = [], wintypes.BOOL
        u.EmptyClipboard.argtypes, u.EmptyClipboard.restype = [], wintypes.BOOL
        u.GetClipboardData.argtypes, u.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
        u.SetClipboardData.argtypes, u.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
        u.IsClipboardFormatAvailable.argtypes, u.IsClipboardFormatAvailable.restype = [wintypes.UINT], wintypes.BOOL
        u.GetClipboardSequenceNumber.argtypes, u.GetClipboardSequenceNumber.restype = [], wintypes.DWORD
        k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
        k.GlobalFree.argtypes, k.GlobalFree.restype = [wintypes.HGLOBAL], wintypes.HGLOBAL
        k.GlobalLock.argtypes, k.GlobalLock.restype = [wintypes.HGLOBAL], wintypes.LPVOID
        k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [wintypes.HGLOBAL], wintypes.BOOL
        self._u, self._k = u, k

    def _open(self) -> bool:
        for _ in range(15):                  # another program may be holding the clipboard for a moment
            if self._u.OpenClipboard(None):
                return True
            time.sleep(0.02)
        return False

    def sequence(self) -> int:
        return int(self._u.GetClipboardSequenceNumber())

    def get(self):
        u, k = self._u, self._k
        if not u.IsClipboardFormatAvailable(CF_UNICODETEXT) or not self._open():
            return None
        try:
            handle = u.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = k.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return self._ct.wstring_at(ptr)
            finally:
                k.GlobalUnlock(handle)
        finally:
            u.CloseClipboard()

    def set(self, text: str) -> bool:
        u, k = self._u, self._k
        data = (text + "\0").encode("utf-16-le")
        handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = k.GlobalLock(handle)
        if not ptr:
            k.GlobalFree(handle)
            return False
        self._ct.memmove(ptr, data, len(data))
        k.GlobalUnlock(handle)
        if not self._open():
            k.GlobalFree(handle)
            return False
        try:
            u.EmptyClipboard()
            if not u.SetClipboardData(CF_UNICODETEXT, handle):
                k.GlobalFree(handle)         # on success Windows owns the memory
                return False
            return True
        finally:
            u.CloseClipboard()


class Clipboard:
    """Text clipboard with Unix-style newlines on the wire and Windows-style ones on the PC."""

    def __init__(self, backend=None):
        if backend is None and IS_WIN:
            try:
                backend = WinBackend()
            except Exception:
                backend = None
        self.backend = backend

    @property
    def available(self) -> bool:
        return self.backend is not None

    def sequence(self) -> int:
        return self.backend.sequence() if self.backend else 0

    def get_text(self):
        """-> the clipboard text, or None when it holds no text."""
        if not self.backend:
            return None
        text = self.backend.get()
        return text.replace("\r\n", "\n") if text else None

    def set_text(self, text: str) -> bool:
        if not self.backend or not text or len(text) > MAX_CHARS:
            return False
        return bool(self.backend.set(text.replace("\r\n", "\n").replace("\n", "\r\n")))
