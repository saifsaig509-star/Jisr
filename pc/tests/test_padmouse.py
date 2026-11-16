"""Tests for the gamepad desktop mode (no Windows needed: the injector is replaced by a recorder)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from btbridge.padmouse import PadMouse
from btbridge.server import Bridge


class Rec:
    """Stands in for inject.Injector and records every call."""
    def __init__(self):
        self.calls = []

    def key(self, code, down):
        self.calls.append(("key", code, down))

    def button(self, btn, down):
        self.calls.append(("btn", btn, down))

    def move(self, dx, dy):
        self.calls.append(("move", dx, dy))

    def scroll(self, v, h=0.0):
        self.calls.append(("scroll", round(v, 3), round(h, 3)))

    def release_all(self):
        pass


class Clock:
    t = 0.0
    def __call__(self):
        return self.t


def unit():
    clock = Clock()
    rec = Rec()
    pm = PadMouse(rec, clock=clock)

    # dead zone: tiny stick movement does nothing
    pm.axis("lx", 0.1); pm.axis("ly", 0.1)
    pm.step(0.1)
    assert rec.calls == [], rec.calls

    # full right deflection for 0.1 s moves right ~110 px, never vertically
    pm.axis("lx", 1.0); pm.axis("ly", 0.0)
    total = 0
    for _ in range(10):
        pm.step(0.01)
    for c in rec.calls:
        assert c[0] == "move" and c[2] == 0, c
        total += c[1]
    assert 100 <= total <= 111, total
    print("pointer moves right, dead zone OK, moved", total, "px in 0.1 s")

    # fractional pixels are not lost: 0.3 deflection for many tiny steps still moves
    rec.calls.clear(); pm.reset()
    pm.axis("lx", 0.5)
    moved = 0
    for _ in range(100):
        pm.step(0.008)
    moved = sum(c[1] for c in rec.calls)
    assert moved > 0, rec.calls
    print("slow stick still moves (remainders kept):", moved, "px")

    # down = +y
    rec.calls.clear(); pm.reset()
    pm.axis("ly", 1.0); pm.step(0.05)
    assert rec.calls and rec.calls[0][0] == "move" and rec.calls[0][2] > 0 and rec.calls[0][1] == 0, rec.calls

    # right stick up = scroll up (positive), right = scroll right (positive)
    rec.calls.clear(); pm.reset()
    pm.axis("ry", -1.0)
    for _ in range(20):
        pm.step(0.01)
    s = [c for c in rec.calls if c[0] == "scroll"]
    assert s and all(c[1] > 0 and c[2] == 0 for c in s), rec.calls
    print("right stick up scrolls up OK")

    # buttons
    rec.calls.clear(); pm.reset()
    pm.button(96, True); pm.button(96, False)          # A
    pm.button(97, True); pm.button(97, False)          # B
    pm.button(99, True); pm.button(99, False)          # X -> Enter
    assert rec.calls == [("btn", 1, True), ("btn", 1, False), ("btn", 2, True), ("btn", 2, False),
                         ("key", 66, True), ("key", 66, False)], rec.calls
    rec.calls.clear()
    pm.button(102, True); pm.button(102, False)        # LB -> Alt+Left
    assert rec.calls == [("key", 57, True), ("key", 21, True), ("key", 21, False), ("key", 57, False)], rec.calls
    rec.calls.clear()
    pm.button(108, True); assert rec.calls == []       # Start acts on release
    pm.button(108, False); assert rec.calls == [("key", 117, True), ("key", 117, False)], rec.calls
    print("button mapping OK")

    # d-pad repeat
    rec.calls.clear(); pm.reset()
    pm.button(22, True)
    clock.t = 0.2; pm.step(0.2); assert rec.calls == [("key", 22, True)], rec.calls      # not yet repeating
    clock.t = 0.5; pm.step(0.3); assert rec.calls.count(("key", 22, True)) == 2, rec.calls
    pm.button(22, False)
    n = len(rec.calls); clock.t = 2.0; pm.step(1.5); assert len(rec.calls) == n, "repeat must stop"
    print("d-pad auto-repeat OK")

    # hat axis + trigger hysteresis
    rec.calls.clear(); pm.reset()
    pm.axis("hx", -1.0); pm.axis("hx", 0.0)
    assert rec.calls == [("key", 21, True), ("key", 21, False)], rec.calls
    rec.calls.clear()
    pm.axis("rt", 0.7); pm.axis("rt", 0.5); pm.axis("rt", 0.7)   # stays pressed until < 0.3
    assert rec.calls == [("key", 24, True)], rec.calls
    pm.axis("rt", 0.1)
    assert rec.calls[-1] == ("key", 24, False), rec.calls
    print("hat + trigger OK")

    # reset releases held things
    rec.calls.clear()
    pm.button(96, True); pm.button(20, True)
    rec.calls.clear(); pm.reset()
    assert ("btn", 1, False) in rec.calls and ("key", 20, False) in rec.calls, rec.calls
    print("reset releases everything OK")


async def bridge_modes():
    modes = []
    b = Bridge("123456", log=lambda m: None, pad_mode="desktop", on_mode=modes.append)
    rec = Rec()
    b.padmouse = PadMouse(rec)
    sent = []

    class FakeGamepad:
        available = True
        def button(self, c, d): sent.append(("pad_btn", c, d))
        def axis(self, n, v): sent.append(("pad_axis", n, v))
        def reset(self): sent.append("reset")
    b.gamepad = FakeGamepad()

    # desktop: A clicks, nothing reaches the virtual pad
    b.pad_button(96, True); b.pad_button(96, False)
    assert rec.calls == [("btn", 1, True), ("btn", 1, False)] and sent == [], (rec.calls, sent)

    # Start + Select -> game mode, chord not forwarded anywhere
    rec.calls.clear()
    b.pad_button(108, True); b.pad_button(109, True)
    assert b.pad_mode == "game" and modes == ["game"], (b.pad_mode, modes)
    b.pad_button(109, False); b.pad_button(108, False)
    assert rec.calls == [], rec.calls                      # no Win/Tab taps from the chord
    assert not [x for x in sent if x != "reset"], sent

    # game: A goes to the virtual pad
    sent.clear()
    b.pad_button(96, True); b.pad_axis("lx", 0.5)
    assert sent == [("pad_btn", 96, True), ("pad_axis", "lx", 0.5)] and rec.calls == [], (sent, rec.calls)

    # and back to desktop; a later plain Start tap works again
    b.pad_button(109, True); b.pad_button(108, True)
    assert b.pad_mode == "desktop" and modes == ["game", "desktop"], modes
    b.pad_button(108, False); b.pad_button(109, False)
    rec.calls.clear()
    b.pad_button(108, True); b.pad_button(108, False)
    assert rec.calls == [("key", 117, True), ("key", 117, False)], rec.calls
    print("mode toggle with Start+Select OK")

    # thread-safe API
    b.set_pad_mode("game"); assert b.pad_mode == "game"
    b.set_pad_mode("nonsense"); assert b.pad_mode == "game"
    print("set_pad_mode OK")

    # the periodic task runs inside the real run loop and stops cleanly
    b2 = Bridge("123456", log=lambda m: None, port=48811)
    b2.padmouse = PadMouse(rec)
    task = asyncio.create_task(b2.run())
    await asyncio.sleep(0.3)
    rec.calls.clear()
    b2.pad_axis("lx", 1.0)
    await asyncio.sleep(0.3)
    assert any(c[0] == "move" and c[1] > 0 for c in rec.calls), rec.calls
    print("run loop moves the pointer:", sum(c[1] for c in rec.calls if c[0] == "move"), "px in 0.3 s")
    b2.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass


unit()
asyncio.run(bridge_modes())
print("\nALL PASSED")
