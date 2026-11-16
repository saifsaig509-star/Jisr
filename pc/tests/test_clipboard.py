import asyncio, json, sys
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btbridge import protocol as P
from btbridge.clipboard import Clipboard, MAX_CHARS
from btbridge.server import Bridge
from phone_client import connect, PIN

PORT = 48810


class FakeBackend:
    """Behaves like the Windows clipboard: stores text, bumps a sequence number on every change."""
    def __init__(self): self.text, self.seq = None, 0
    def sequence(self): return self.seq
    def get(self): return self.text
    def set(self, text): self.text, self.seq = text, self.seq + 1; return True


async def msg(phone, timeout=3):
    return await phone.jframe(timeout)


async def main():
    fake = FakeBackend()
    logs = []
    b = Bridge(PIN, log=logs.append, port=PORT)
    b.clipboard = Clipboard(fake)
    srv = asyncio.create_task(b.run())
    await asyncio.sleep(0.3)

    phone, hello = await connect()
    assert hello["t"] == "hello_ok" and hello["caps"]["clipboard"] is True, hello
    print("caps.clipboard advertised OK")

    # phone -> PC (Arabic, emoji, newlines): arrives on the PC clipboard with Windows newlines
    text = "مرحبا بالعالم\nسطر ثان 😀\nline 3"
    phone.send_json({"t": "clip", "s": text}); await phone.w.drain()
    ack = await msg(phone); assert ack == {"t": "clip_ok", "n": len(text)}, ack
    assert fake.text == text.replace("\n", "\r\n"), repr(fake.text)
    print("phone -> PC clipboard OK (CRLF on the PC side)")

    # PC -> phone on request: newlines come back as \n
    fake.set("copied on the PC\r\nsecond line")
    phone.send_json({"t": "clip_get"}); await phone.w.drain()
    m = await msg(phone); assert m == {"t": "clip", "s": "copied on the PC\nsecond line"}, m
    print("clip_get OK")

    # empty clipboard / too long text -> clear errors, nothing breaks
    fake.text = None
    phone.send_json({"t": "clip_get"}); await phone.w.drain()
    assert (await msg(phone))["t"] == "clip_error"
    phone.send_json({"t": "clip", "s": "x" * (MAX_CHARS + 1)}); await phone.w.drain()
    assert (await msg(phone))["t"] == "clip_error"
    phone.send_json({"t": "clip", "s": ""}); await phone.w.drain()
    assert (await msg(phone))["t"] == "clip_error"
    print("errors (empty / too long) OK")

    # automatic sync: off by default, then on -> a PC copy shows up on the phone by itself
    fake.set("copied while sync is OFF")
    try:
        await asyncio.wait_for(phone.frame(), 1.2); raise SystemExit("FAIL: sent while sync was off")
    except asyncio.TimeoutError:
        pass
    phone.send_json({"t": "clip_sync", "on": True}); await phone.w.drain()
    await asyncio.sleep(0.2)
    fake.set("auto-synced text")
    m = await msg(phone); assert m == {"t": "clip", "s": "auto-synced text"}, m
    print("auto sync PC -> phone OK")

    # echo guard: what the phone just sent must not bounce back
    phone.send_json({"t": "clip", "s": "from phone, no echo"}); await phone.w.drain()
    assert (await msg(phone))["t"] == "clip_ok"
    try:
        await asyncio.wait_for(phone.frame(), 1.5); raise SystemExit("FAIL: echoed back")
    except asyncio.TimeoutError:
        print("no echo OK")

    # the same text copied twice in a row is sent once
    fake.set("same"); m = await msg(phone); assert m["s"] == "same"
    fake.set("same")
    try:
        await asyncio.wait_for(phone.frame(), 1.5); raise SystemExit("FAIL: duplicate sent")
    except asyncio.TimeoutError:
        print("duplicate suppressed OK")

    # sync off again
    phone.send_json({"t": "clip_sync", "on": False}); await phone.w.drain()
    await asyncio.sleep(0.2)
    fake.set("after off")
    try:
        await asyncio.wait_for(phone.frame(), 1.2); raise SystemExit("FAIL: sent after sync off")
    except asyncio.TimeoutError:
        print("sync off OK")

    # no clipboard on this PC (e.g. not Windows): a clear error instead of a crash
    b.clipboard = Clipboard(None)
    phone2, hello2 = await connect(); assert hello2["caps"]["clipboard"] is False
    phone2.send_json({"t": "clip", "s": "hi"}); await phone2.w.drain()
    assert (await phone2.jframe())["t"] == "clip_error"
    print("unavailable clipboard -> clip_error OK")

    # the watcher stops when the phone disconnects
    phone3, _ = await connect(); b.clipboard = Clipboard(fake)
    phone3.send_json({"t": "clip_sync", "on": True}); await phone3.w.drain()
    await asyncio.sleep(0.3)
    sess = b.session; assert sess.clip_task is not None
    phone3.w.close(); await asyncio.sleep(0.5)
    assert sess.clip_task is None; print("watcher stopped on disconnect OK")

    b.stop()
    try: await srv
    except asyncio.CancelledError: pass
    print("\nALL PASSED")

asyncio.run(main())
