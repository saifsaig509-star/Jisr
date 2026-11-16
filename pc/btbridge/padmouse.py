"""Desktop mode for a gamepad: use the pad as a mouse + keyboard instead of a virtual Xbox pad.

Mapping (Android keycodes as sent by the phone):

  left stick .... mouse pointer          right stick ... scroll (vertical / horizontal)
  A ............. left click (hold = drag)   B ......... right click
  X ............. Enter                       Y ......... Esc
  D-pad ......... arrow keys (auto repeat)    L3 ....... middle click
  LB / RB ....... Alt+Left / Alt+Right (browser back / forward)
  LT / RT ....... volume down / up
  Start ......... Windows key (tap)           Select ... Tab (tap)

Start + Select pressed together switch between desktop mode and game mode (see server.Bridge).
"""
import asyncio
import math

# gamepad button codes (Android KeyEvent)
BTN_A, BTN_B, BTN_X, BTN_Y = 96, 97, 99, 100
BTN_L1, BTN_R1, BTN_L2, BTN_R2 = 102, 103, 104, 105
BTN_L3 = 106
BTN_START, BTN_SELECT = 108, 109
DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT = 19, 20, 21, 22

# keycodes understood by Injector.KEYMAP
K_ESC, K_ENTER, K_TAB, K_ALT, K_WIN = 111, 66, 61, 57, 117
K_VOL_UP, K_VOL_DOWN = 24, 25

REPEAT_DELAY = 0.40          # seconds before a held key starts repeating
REPEAT_EVERY = 0.06


class PadMouse:
    def __init__(self, injector, speed: float = 1100.0, scroll_speed: float = 9.0,
                 deadzone: float = 0.18, clock=None):
        self.inj = injector
        self.speed = speed                  # pixels / second at full deflection
        self.scroll_speed = scroll_speed    # wheel notches / second at full deflection
        self.deadzone = deadzone
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self.reset()

    # ---- state ----------------------------------------------------------
    def reset(self):
        """Release everything this object pressed and zero the sticks."""
        for b in getattr(self, "_btns", ()):
            self.inj.button(b, False)
        for code in list(getattr(self, "_repeat", {})):
            self.inj.key(code, False)
        self._btns = set()
        self._repeat = {}                   # android keycode -> next auto-repeat time
        self.lx = self.ly = self.rx = self.ry = 0.0
        self._rem = [0.0, 0.0, 0.0, 0.0]    # fractional pixels (x, y) and wheel notches (v, h)
        self._trig = {"lt": False, "rt": False}
        self._hat = {"hx": 0, "hy": 0}

    # ---- helpers --------------------------------------------------------
    def _key_down(self, code, repeat=False):
        self.inj.key(code, True)
        if repeat:
            self._repeat[code] = self._clock() + REPEAT_DELAY

    def _key_up(self, code):
        self._repeat.pop(code, None)
        self.inj.key(code, False)

    def _tap(self, code):
        self.inj.key(code, True)
        self.inj.key(code, False)

    def _chord(self, mod, code):
        self.inj.key(mod, True)
        self._tap(code)
        self.inj.key(mod, False)

    def _mouse(self, btn, down):
        (self._btns.add if down else self._btns.discard)(btn)
        self.inj.button(btn, down)

    # ---- input ----------------------------------------------------------
    def button(self, code: int, down: bool):
        if code == BTN_A:
            self._mouse(1, down)
        elif code == BTN_B:
            self._mouse(2, down)
        elif code == BTN_L3:
            self._mouse(3, down)
        elif code == BTN_X:
            self._key_down(K_ENTER) if down else self._key_up(K_ENTER)
        elif code == BTN_Y:
            self._key_down(K_ESC) if down else self._key_up(K_ESC)
        elif code in (DPAD_UP, DPAD_DOWN, DPAD_LEFT, DPAD_RIGHT):
            self._key_down(code, repeat=True) if down else self._key_up(code)
        elif code == BTN_L1 and down:
            self._chord(K_ALT, DPAD_LEFT)
        elif code == BTN_R1 and down:
            self._chord(K_ALT, DPAD_RIGHT)
        elif code in (BTN_L2, BTN_R2):
            self._volume(K_VOL_DOWN if code == BTN_L2 else K_VOL_UP, down)
        elif code == BTN_START and not down:        # act on release so Start+Select can be a chord
            self._tap(K_WIN)
        elif code == BTN_SELECT and not down:
            self._tap(K_TAB)

    def _volume(self, code, down):
        self._key_down(code, repeat=True) if down else self._key_up(code)

    def axis(self, name: str, value: float):
        value = max(-1.0, min(1.0, float(value)))
        if name == "lx":
            self.lx = value
        elif name == "ly":
            self.ly = value
        elif name == "rx":
            self.rx = value
        elif name == "ry":
            self.ry = value
        elif name in ("lt", "rt"):                  # analog triggers -> volume, with hysteresis
            was = self._trig[name]
            now = value > (0.3 if was else 0.6)
            if now != was:
                self._trig[name] = now
                self._volume(K_VOL_DOWN if name == "lt" else K_VOL_UP, now)
        elif name in ("hx", "hy"):                  # hat axes -> arrow keys
            neg, pos = (DPAD_LEFT, DPAD_RIGHT) if name == "hx" else (DPAD_UP, DPAD_DOWN)
            new = -1 if value < -0.5 else (1 if value > 0.5 else 0)
            old = self._hat[name]
            if new != old:
                if old:
                    self._key_up(neg if old < 0 else pos)
                if new:
                    self._key_down(neg if new < 0 else pos, repeat=True)
                self._hat[name] = new

    # ---- periodic update --------------------------------------------------
    def _stick(self, x, y):
        """Dead zone + quadratic response: precise near the centre, fast at the edge."""
        m = math.hypot(x, y)
        if m < self.deadzone:
            return 0.0, 0.0
        s = min(1.0, (m - self.deadzone) / (1.0 - self.deadzone)) ** 2
        return x / m * s, y / m * s

    def step(self, dt: float):
        now = self._clock()
        rem = self._rem
        # pointer
        sx, sy = self._stick(self.lx, self.ly)
        rem[0] += sx * self.speed * dt
        rem[1] += sy * self.speed * dt
        dx, dy = int(rem[0]), int(rem[1])
        if dx or dy:
            rem[0] -= dx
            rem[1] -= dy
            self.inj.move(dx, dy)
        # wheel (stick up = scroll up)
        wx, wy = self._stick(self.rx, self.ry)
        rem[2] += -wy * self.scroll_speed * dt
        rem[3] += wx * self.scroll_speed * dt
        if abs(rem[2]) >= 0.2 or abs(rem[3]) >= 0.2:
            self.inj.scroll(rem[2], rem[3])
            rem[2] = rem[3] = 0.0
        # key auto-repeat
        for code, due in list(self._repeat.items()):
            if now >= due:
                self.inj.key(code, True)
                self._repeat[code] = now + REPEAT_EVERY

    async def run(self, tick: float = 0.008):
        loop = asyncio.get_running_loop()
        last = loop.time()
        while True:
            await asyncio.sleep(tick)
            now = loop.time()
            self.step(min(now - last, 0.1))
            last = now
