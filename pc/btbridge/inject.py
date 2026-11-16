"""Keyboard / mouse injection on Windows (ctypes + SendInput, no dependencies).

On other platforms the injector is a no-op so the rest of the program can be
developed and tested anywhere.
"""
import sys

IS_WIN = sys.platform == "win32"

# Android KeyEvent keycode -> Windows virtual-key code
KEYMAP = {}
for i in range(10):
    KEYMAP[7 + i] = 0x30 + i            # 0-9
for i in range(26):
    KEYMAP[29 + i] = 0x41 + i           # A-Z
for i in range(12):
    KEYMAP[131 + i] = 0x70 + i          # F1-F12
for i in range(10):
    KEYMAP[144 + i] = 0x60 + i          # numpad 0-9
KEYMAP.update({
    19: 0x26, 20: 0x28, 21: 0x25, 22: 0x27,          # arrows
    24: 0xAF, 25: 0xAE, 164: 0xAD,                   # volume up/down/mute
    55: 0xBC, 56: 0xBE,                              # , .
    57: 0xA4, 58: 0xA5,                              # alt L/R
    59: 0xA0, 60: 0xA1,                              # shift L/R
    61: 0x09, 62: 0x20, 66: 0x0D, 67: 0x08,          # tab space enter backspace
    68: 0xC0, 69: 0xBD, 70: 0xBB, 71: 0xDB, 72: 0xDD,
    73: 0xDC, 74: 0xBA, 75: 0xDE, 76: 0xBF,          # punctuation keys
    82: 0x5D,                                        # menu / apps
    85: 0xB3, 86: 0xB2, 87: 0xB0, 88: 0xB1,          # media play/stop/next/prev
    126: 0xB3, 127: 0xB3,
    92: 0x21, 93: 0x22,                              # page up/down
    111: 0x1B, 112: 0x2E,                            # esc, delete
    113: 0xA2, 114: 0xA3,                            # ctrl L/R
    115: 0x14, 116: 0x91,                            # caps lock, scroll lock
    117: 0x5B, 118: 0x5C,                            # win L/R
    121: 0x13,                                       # pause/break
    122: 0x24, 123: 0x23, 124: 0x2D,                 # home end insert
    143: 0x90,                                       # num lock
    154: 0x6F, 155: 0x6A, 156: 0x6D, 157: 0x6B, 158: 0x6E,
    160: 0x0D,                                       # numpad enter
})
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0xA3, 0xA5,
            0x5B, 0x5C, 0x5D, 0x6F, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    _user32 = ctypes.windll.user32
    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
    MOUSEEVENTF = {"move": 0x0001, "wheel": 0x0800, "hwheel": 0x1000}
    BTN_FLAGS = {1: (0x0002, 0x0004), 2: (0x0008, 0x0010), 3: (0x0020, 0x0040)}  # down, up

    def _send(inp):
        _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Injector:
    def __init__(self):
        self.available = IS_WIN
        self._keys = set()
        self._buttons = set()

    # ---- keyboard -------------------------------------------------------
    def key(self, android_code: int, down: bool):
        vk = KEYMAP.get(android_code)
        if vk is None:
            return
        (self._keys.add if down else self._keys.discard)(vk)
        self._key_vk(vk, down)

    def _key_vk(self, vk: int, down: bool):
        if not IS_WIN:
            return
        flags = 0 if down else KEYEVENTF_KEYUP
        if vk in EXTENDED:
            flags |= KEYEVENTF_EXTENDEDKEY
        scan = _user32.MapVirtualKeyW(vk, 0)
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        _send(inp)

    # ---- text (any language, typed as Unicode) ---------------------------
    MODIFIER_VKS = {0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C}      # ctrl, alt, win (left / right)
    SHIFT_VKS = {0xA0, 0xA1}
    MAX_TEXT = 1000

    def type_text(self, text: str):
        """Type `text` as if typed on a keyboard. Newline = Enter, tab = Tab.

        Letters are sent as Unicode characters, so Arabic and every other script work whatever the
        PC keyboard layout is. While Ctrl / Alt / Win is held, ASCII characters are sent as real
        keys instead, so shortcuts such as Ctrl+C, Ctrl+V and Ctrl+A work.
        """
        text = text[:self.MAX_TEXT].replace("\r\n", "\n")
        chord = bool(self._keys & self.MODIFIER_VKS)
        for ch in text:
            if ch in "\n\r":
                self._tap_vk(0x0D)
            elif ch == "\t":
                self._tap_vk(0x09)
            elif chord and ch.isascii() and self._type_chord_char(ch):
                pass
            else:
                data = ch.encode("utf-16-le", "surrogatepass")
                for i in range(0, len(data), 2):                 # a character outside the BMP = 2 units
                    unit = data[i] | (data[i + 1] << 8)
                    self._unicode(unit, True)
                    self._unicode(unit, False)

    def _type_chord_char(self, ch: str) -> bool:
        """Send an ASCII character as a real key press (for Ctrl+<key>). False if it has no key."""
        res = self._vk_for_char(ch)
        if res is None:
            return False
        vk, needs_shift = res
        shift = needs_shift and not (self._keys & self.SHIFT_VKS)
        if shift:
            self._key_vk(0xA0, True)
        self._tap_vk(vk)
        if shift:
            self._key_vk(0xA0, False)
        return True

    def _tap_vk(self, vk: int):
        self._key_vk(vk, True)
        self._key_vk(vk, False)

    def _vk_for_char(self, ch: str):
        """(virtual key, needs shift) for a character on the current layout, or None."""
        if not IS_WIN:
            return None
        r = _user32.VkKeyScanW(ord(ch))
        if r == -1 or (r & 0xFF) == 0xFF:
            return None
        return r & 0xFF, bool((r >> 8) & 1)

    def _unicode(self, unit: int, down: bool):
        if not IS_WIN:
            return
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (0 if down else KEYEVENTF_KEYUP), 0, 0)
        _send(inp)

    # ---- mouse ----------------------------------------------------------
    def move(self, dx: int, dy: int):
        if not IS_WIN or (dx == 0 and dy == 0):
            return
        inp = INPUT(type=INPUT_MOUSE)
        inp.u.mi = MOUSEINPUT(int(dx), int(dy), 0, MOUSEEVENTF["move"], 0, 0)
        _send(inp)

    def button(self, btn: int, down: bool):
        if btn not in (1, 2, 3):
            return
        (self._buttons.add if down else self._buttons.discard)(btn)
        self._button(btn, down)

    def _button(self, btn: int, down: bool):
        if not IS_WIN:
            return
        flags = BTN_FLAGS[btn][0 if down else 1]
        inp = INPUT(type=INPUT_MOUSE)
        inp.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
        _send(inp)

    def scroll(self, vertical: float, horizontal: float = 0.0):
        if not IS_WIN:
            return
        for amount, flag in ((vertical, "wheel"), (horizontal, "hwheel")):
            if amount:
                inp = INPUT(type=INPUT_MOUSE)
                inp.u.mi = MOUSEINPUT(0, 0, int(amount * 120) & 0xFFFFFFFF, MOUSEEVENTF[flag], 0, 0)
                _send(inp)

    # ---- safety ---------------------------------------------------------
    def release_all(self):
        """Called when the phone disconnects so no key/button stays stuck."""
        for vk in list(self._keys):
            self._key_vk(vk, False)
        for b in list(self._buttons):
            self._button(b, False)
        self._keys.clear()
        self._buttons.clear()
