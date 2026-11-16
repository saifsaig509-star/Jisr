"""Virtual Xbox 360 gamepad through ViGEmBus (optional).

Install:  pip install vgamepad   (it asks you to install the ViGEmBus driver)
"""
try:
    import vgamepad as vg
except Exception:  # not installed / not Windows
    vg = None


class Gamepad:
    def __init__(self, log=print):
        self.log = log
        self.pad = None
        self.failed = vg is None
        self._stick = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}

    @property
    def available(self) -> bool:
        return not self.failed

    def _ensure(self):
        if self.pad is None and not self.failed:
            try:
                self.pad = vg.VX360Gamepad()
                self.log("Virtual gamepad created")
            except Exception as e:
                self.failed = True
                self.log(f"Virtual gamepad unavailable (install ViGEmBus?): {e}")
        return self.pad

    # Android keycode -> XUSB button name
    BUTTONS = {
        96: "A", 97: "B", 99: "X", 100: "Y",
        102: "LEFT_SHOULDER", 103: "RIGHT_SHOULDER",
        106: "LEFT_THUMB", 107: "RIGHT_THUMB",
        108: "START", 109: "BACK", 110: "GUIDE",
        19: "DPAD_UP", 20: "DPAD_DOWN", 21: "DPAD_LEFT", 22: "DPAD_RIGHT",
    }

    def _btn(self, name, down):
        pad = self._ensure()
        if not pad:
            return
        b = getattr(vg.XUSB_BUTTON, f"XUSB_GAMEPAD_{name}")
        (pad.press_button if down else pad.release_button)(button=b)
        pad.update()

    def button(self, code: int, down: bool):
        if code in (104, 105):  # L2 / R2 as digital buttons
            self.axis("lt" if code == 104 else "rt", 1.0 if down else 0.0)
            return
        name = self.BUTTONS.get(code)
        if name:
            self._btn(name, down)

    def axis(self, name: str, value: float):
        pad = self._ensure()
        if not pad:
            return
        s = self._stick
        if name in ("lx", "ly"):
            s["l" + name[1]] = value
            pad.left_joystick_float(s["lx"], -s["ly"])
        elif name in ("rx", "ry"):
            s["r" + name[1]] = value
            pad.right_joystick_float(s["rx"], -s["ry"])
        elif name == "lt":
            pad.left_trigger_float(max(0.0, min(1.0, value)))
        elif name == "rt":
            pad.right_trigger_float(max(0.0, min(1.0, value)))
        elif name == "hx":
            self._hat("DPAD_LEFT", value < -0.5)
            self._hat("DPAD_RIGHT", value > 0.5)
            return
        elif name == "hy":
            self._hat("DPAD_UP", value < -0.5)
            self._hat("DPAD_DOWN", value > 0.5)
            return
        pad.update()

    def _hat(self, name, down):
        self._btn(name, down)

    def reset(self):
        if self.pad:
            try:
                self.pad.reset()
                self.pad.update()
            except Exception:
                pass
        self._stick = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}
