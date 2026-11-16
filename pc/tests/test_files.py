"""File transfer: the real server against a fake phone written independently (tests/phone_client.py)."""
import asyncio, hashlib, json, os, shutil, struct, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btbridge import protocol as P
from btbridge import files as F
from btbridge.server import Bridge
from phone_client import connect, PIN

PORT = 48810
ID = struct.Struct(">I")


def sha(b): return hashlib.sha256(b).hexdigest()


async def upload(phone, fid, name, data, chunk=40000, accept_check=True, done_sha=None, done_size=None):
    """The phone sends a file: offer -> accept -> chunks -> done. Returns the PC's final answer."""
    phone.send_json({"t": "file_offer", "id": fid, "name": name, "size": len(data)}); await phone.w.drain()
    r = await phone.jframe()
    if r["t"] != "file_accept":
        return r
    assert r == {"t": "file_accept", "id": fid}, r
    for i in range(0, len(data), chunk):
        phone.send(P.T_FILE, ID.pack(fid) + data[i:i + chunk])
    await phone.w.drain()
    phone.send_json({"t": "file_done", "id": fid, "size": len(data) if done_size is None else done_size,
                     "sha256": done_sha or sha(data)}); await phone.w.drain()
    return await phone.jframe(10)


async def next_json(phone, timeout=5):
    """Next JSON frame, skipping pongs etc."""
    while True:
        t, d = await phone.frame(timeout)
        if t == P.T_JSON:
            m = json.loads(d)
            if m.get("t") != "pong":
                return m


def unit_tests():
    n = F.safe_name
    assert n("hello.txt") == "hello.txt"
    assert n("..\\..\\Windows\\evil.dll") == "evil.dll"
    assert n("../../etc/passwd") == "passwd"
    assert n("C:\\Users\\x\\a.doc") == "a.doc"
    assert n("..") == "file" and n("...") == "file" and n("") == "file" and n("   ") == "file"
    assert n("CON") == "_CON" and n("con.txt") == "_con.txt" and n("lpt1.tar.gz") == "_lpt1.tar.gz"
    assert n("a<b>c:d\"e|f?g*h.txt") == "a_b_c_d_e_f_g_h.txt"
    assert n("bad\x00name\n.txt") == "bad_name_.txt"
    assert n("report \u202egnp.exe") == "report gnp.exe", repr(n("report \u202egnp.exe"))   # bidi override removed
    assert n("تقرير المبيعات ٢٠٢٦.pdf") == "تقرير المبيعات ٢٠٢٦.pdf"
    assert n("name. . ") == "name"
    long = n("x" * 300 + ".jpeg"); assert len(long) <= 120 and long.endswith(".jpeg"), len(long)
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("1"); (d / "a (1).txt.part").write_text("2")
    assert F.unique_path(d, "a.txt").name == "a (2).txt", F.unique_path(d, "a.txt")
    assert F.unique_path(d, "b.txt").name == "b.txt"
    shutil.rmtree(d)
    assert F.human(0) == "0 B" and F.human(1536) == "1.5 KB" and F.human(5 << 30) == "5.0 GB"
    print("unit: safe_name / unique_path / human OK")


async def main():
    unit_tests()
    folder = Path(tempfile.mkdtemp(prefix="btb_recv_"))
    logs, events = [], []
    b = Bridge(PIN, log=logs.append, port=PORT, receive_dir=folder)
    b.file_cb = events.append
    srv = asyncio.create_task(b.run())
    await asyncio.sleep(0.3)

    phone, hello = await connect()
    assert hello["t"] == "hello_ok" and hello["caps"]["files"] is True, hello
    print("caps.files advertised OK")

    # ---------------- phone -> PC
    small = "مرحبا من الهاتف\nhello".encode()
    r = await upload(phone, 1, "ملاحظة.txt", small)
    assert r["t"] == "file_ok" and r["id"] == 1 and r["name"] == "ملاحظة.txt" and r["size"] == len(small), r
    assert (folder / "ملاحظة.txt").read_bytes() == small
    assert not list(folder.glob("*.part"))
    print("phone -> PC small file (Arabic name) OK")

    big = os.urandom(5 * 1024 * 1024 + 123)
    r = await upload(phone, 3, "big.bin", big, chunk=F.CHUNK)
    assert r["t"] == "file_ok", r
    assert (folder / "big.bin").read_bytes() == big
    ev = [e for e in events if e["t"] == "progress" and e["name"] == "big.bin"]
    assert ev and ev[-1]["done"] == len(big), ev[-1:]
    print("phone -> PC 5 MB file, SHA-256 verified, progress events OK")

    r = await upload(phone, 5, "empty.txt", b"")
    assert r["t"] == "file_ok" and (folder / "empty.txt").read_bytes() == b"", r
    print("empty file OK")

    # same name again -> never overwrites
    r = await upload(phone, 7, "big.bin", b"second")
    assert r["t"] == "file_ok" and r["name"] == "big.bin".replace(".bin", " (1).bin"), r
    assert (folder / "big.bin").read_bytes() == big and (folder / "big (1).bin").read_bytes() == b"second"
    print("duplicate name -> 'name (1)' (nothing overwritten) OK")

    # hostile names stay inside the folder
    for fid, evil in ((9, "..\\..\\evil.txt"), (11, "../../evil2.txt"), (13, "C:\\Windows\\evil3.dll"), (15, "NUL.txt")):
        r = await upload(phone, fid, evil, b"x")
        assert r["t"] == "file_ok", r
    names = {p.name for p in folder.iterdir()}
    assert {"evil.txt", "evil2.txt", "evil3.dll", "_NUL.txt"} <= names, names
    assert not (folder.parent / "evil.txt").exists() and not (folder.parent.parent / "evil.txt").exists()
    print("path traversal / reserved names neutralised OK")

    # damaged transfer: wrong checksum / wrong size -> file_error and nothing left behind
    before = {p.name for p in folder.iterdir()}
    r = await upload(phone, 17, "bad.bin", b"abcdef", done_sha="00" * 32)
    assert r["t"] == "file_error" and "damaged" in r["msg"], r
    r = await upload(phone, 19, "short.bin", b"abcdef", done_size=99)
    assert r["t"] == "file_error", r
    assert {p.name for p in folder.iterdir()} == before, "partial/damaged file left behind"
    print("checksum / size mismatch -> file_error, nothing left OK")

    # more data than announced
    phone.send_json({"t": "file_offer", "id": 21, "name": "over.bin", "size": 10}); await phone.w.drain()
    assert (await phone.jframe())["t"] == "file_accept"
    phone.send(P.T_FILE, ID.pack(21) + b"x" * 50); await phone.w.drain()
    r = await phone.jframe(); assert r["t"] == "file_error" and r["id"] == 21, r
    assert {p.name for p in folder.iterdir()} == before
    print("more data than announced -> file_error OK")

    # refusals
    r = await upload(phone, 23, "huge.iso", b"", ); assert r["t"] == "file_ok"     # control: fine
    phone.send_json({"t": "file_offer", "id": 25, "name": "huge.iso", "size": F.MAX_SIZE + 1}); await phone.w.drain()
    r = await phone.jframe(); assert r["t"] == "file_reject" and "8 GB" in r["msg"], r
    phone.send_json({"t": "file_offer", "id": 26, "name": "even-id.txt", "size": 1}); await phone.w.drain()
    r = await phone.jframe(); assert r["t"] == "file_reject", r              # phone ids must be odd
    phone.send_json({"t": "file_offer", "id": 27, "name": "x", "size": -5}); await phone.w.drain()
    assert (await phone.jframe())["t"] == "file_reject"
    phone.send_json({"t": "file_offer", "id": 29, "name": 12345, "size": 1}); await phone.w.drain()
    assert (await phone.jframe())["t"] == "file_reject"
    phone.send_json({"t": "file_offer", "id": "x", "name": "a", "size": 1}); await phone.w.drain()   # ignored, no crash
    for fid in (31, 33, 35, 37):                                                # MAX_INCOMING = 4
        phone.send_json({"t": "file_offer", "id": fid, "name": f"c{fid}", "size": 5}); await phone.w.drain()
        assert (await phone.jframe())["t"] == "file_accept"
    phone.send_json({"t": "file_offer", "id": 39, "name": "c39", "size": 5}); await phone.w.drain()
    r = await phone.jframe(); assert r["t"] == "file_reject" and "too many" in r["msg"], r
    print("refusals (too big, even id, bad values, too many at once) OK")

    # cancel mid-way by the phone: the partial files disappear
    phone.send(P.T_FILE, ID.pack(31) + b"12"); await phone.w.drain()
    await asyncio.sleep(0.2)
    assert any(p.name.endswith(".part") for p in folder.iterdir())
    for fid in (31, 33, 35, 37):
        phone.send_json({"t": "file_cancel", "id": fid}); await phone.w.drain()
    await asyncio.sleep(0.3)
    assert not list(folder.glob("*.part")), list(folder.iterdir())
    print("cancel by the phone -> partial files removed OK")

    # connection lost mid-way: partial file removed, server keeps working
    phone.send_json({"t": "file_offer", "id": 41, "name": "drop.bin", "size": 1000000}); await phone.w.drain()
    assert (await phone.jframe())["t"] == "file_accept"
    phone.send(P.T_FILE, ID.pack(41) + b"y" * 1000); await phone.w.drain()
    await asyncio.sleep(0.2)
    assert (folder / "drop.bin.part").exists()
    phone.w.close(); await asyncio.sleep(0.6)
    assert not list(folder.glob("*.part")), list(folder.iterdir())
    print("connection lost mid-transfer -> partial file removed OK")

    # the PC user presses Cancel while the phone is streaming at full speed: the session must survive
    phone, hello = await connect(); assert hello["t"] == "hello_ok"
    real_write = F.FileTransfers._write
    def slow_write(inc, data):                # keeps a write "in flight" in its worker thread, so the cancel lands in the middle
        import time; time.sleep(0.01); real_write(inc, data)
    F.FileTransfers._write = staticmethod(slow_write)
    for rnd in range(8):
        fid = 101 + 2 * rnd
        phone.send_json({"t": "file_offer", "id": fid, "name": f"race{rnd}.bin", "size": 20 << 20}); await phone.w.drain()
        assert (await next_json(phone))["t"] == "file_accept"
        for i in range(60):
            phone.send(P.T_FILE, ID.pack(fid) + b"z" * F.CHUNK)
        await phone.w.drain()
        b.cancel_files()
        for i in range(60):
            phone.send(P.T_FILE, ID.pack(fid) + b"z" * F.CHUNK)      # stragglers after the cancel are ignored
        await phone.w.drain()
        got = None
        while got is None:
            m = await next_json(phone, 5)
            if m["t"] == "file_cancel": got = m
        assert got["id"] == fid, got
    phone.send_json({"t": "ping"}); await phone.w.drain()
    t, d = await phone.frame(5); assert t == P.T_JSON and json.loads(d)["t"] == "pong"
    assert not list(folder.glob("race*")), list(folder.iterdir())
    F.FileTransfers._write = staticmethod(real_write)
    print("PC cancel while the phone streams (writes in flight, 8 rounds): session alive, no files left OK")

    # ---------------- PC -> phone
    phone, hello = await connect(); assert hello["t"] == "hello_ok"
    src = Path(tempfile.mkdtemp(prefix="btb_src_"))
    f1 = src / "from_pc.bin"; f1.write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    f2 = src / "ملف.txt"; f2.write_text("نص عربي", encoding="utf-8")
    f3 = src / "empty.dat"; f3.write_bytes(b"")

    async def pc_to_phone(phone, expect, accept=True):
        """Receive one file from the PC as a phone would; returns bytes (or None when refused)."""
        m = await next_json(phone, 8)
        assert m["t"] == "file_offer" and m["id"] % 2 == 0, m
        assert m["name"] == expect.name and m["size"] == expect.stat().st_size, m
        if not accept:
            phone.send_json({"t": "file_reject", "id": m["id"], "msg": "no thanks"}); await phone.w.drain()
            return None
        phone.send_json({"t": "file_accept", "id": m["id"]}); await phone.w.drain()
        got = b""
        while True:
            t, d = await phone.frame(8)
            if t == P.T_FILE:
                assert ID.unpack_from(d)[0] == m["id"] and len(d) - 4 <= F.CHUNK
                got += d[4:]
            elif t == P.T_JSON:
                j = json.loads(d)
                if j["t"] == "file_done":
                    assert j["size"] == len(got) and j["sha256"] == sha(got), (j, len(got))
                    phone.send_json({"t": "file_ok", "id": m["id"], "name": m["name"], "size": len(got)})
                    await phone.w.drain()
                    return got

    async def sf(paths):                  # Bridge.send_files is for other threads (the window): never call it on the loop
        return await asyncio.to_thread(b.send_files, paths)

    n, errs = await sf([str(f1), str(f2), str(f3), str(src / "missing.txt"), str(src)])
    assert n == 3 and len(errs) == 2, (n, errs)
    got1 = await pc_to_phone(phone, f1); assert got1 == f1.read_bytes()
    got2 = await pc_to_phone(phone, f2); assert got2 == f2.read_bytes()
    got3 = await pc_to_phone(phone, f3); assert got3 == b""
    print("PC -> phone: 3 files queued and sent one after another, checksums OK;", len(errs), "bad paths reported")

    # the phone refuses -> no data is sent, the next file still goes
    n, _ = await sf([str(f1), str(f2)])
    await pc_to_phone(phone, f1, accept=False)
    got2 = await pc_to_phone(phone, f2); assert got2 == f2.read_bytes()
    print("PC -> phone: refused file skipped, next one sent OK")

    # the phone cancels in the middle of a big file: the PC stops sending
    big_src = src / "huge.bin"; big_src.write_bytes(os.urandom(20 * 1024 * 1024))
    await sf([str(big_src)])
    m = await next_json(phone, 8)
    phone.send_json({"t": "file_accept", "id": m["id"]}); await phone.w.drain()
    chunks = 0
    while chunks < 5:
        t, d = await phone.frame(8)
        chunks += (t == P.T_FILE)
    phone.send_json({"t": "file_cancel", "id": m["id"], "msg": "user cancelled"}); await phone.w.drain()
    await asyncio.sleep(0.8)
    seen = 0
    try:
        while True:
            t, d = await phone.frame(0.7)
            if t == P.T_FILE: seen += 1
    except asyncio.TimeoutError:
        pass
    assert seen < 320 - 5, f"the PC kept sending after the cancel ({seen} chunks)"
    print(f"PC -> phone: cancel from the phone stops the stream (only {seen} extra chunks) OK")

    # the user cancels on the PC while sending
    await sf([str(big_src)])
    m = await next_json(phone, 8)
    phone.send_json({"t": "file_accept", "id": m["id"]}); await phone.w.drain()
    for _ in range(3): await phone.frame(8)
    b.cancel_files()
    got_cancel = False
    for _ in range(400):
        t, d = await phone.frame(5)
        if t == P.T_JSON and json.loads(d).get("t") == "file_cancel":
            got_cancel = True; break
    assert got_cancel
    print("PC -> phone: cancel on the PC tells the phone OK")

    # no phone connected -> clear error
    phone.w.close(); await asyncio.sleep(0.6)
    n, errs = await sf([str(f2)]); assert n == 0 and "phone" in errs[0].lower(), (n, errs)
    print("no phone connected -> clear error OK")

    # ---------------- 'Send to' through the local API (needs the PIN)
    phone, hello = await connect(); assert hello["t"] == "hello_ok"
    async def local(msg):
        r, w = await asyncio.open_connection("127.0.0.1", P.LOCAL_API_PORT)
        w.write((json.dumps(msg) + "\n").encode()); await w.drain()
        line = await asyncio.wait_for(r.readline(), 5); w.close()
        return json.loads(line)
    bad = await local({"t": "send_file", "pin": "000000", "paths": [str(f2)]})
    assert bad["ok"] is False and "PIN" in bad["msg"], bad
    good = await local({"t": "send_file", "pin": PIN, "paths": [str(f2), str(src / "nope")]})
    assert good["ok"] is True and good["queued"] == 1 and good["msg"], good
    assert await pc_to_phone(phone, f2) == f2.read_bytes()
    print("local API send_file: wrong PIN refused, right PIN queued and delivered OK")

    # the command-line helper behind Explorer's "Send to" menu
    from btbridge import sendcli, sendto, config
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="btb_appdata_")
    ok, msg = await asyncio.to_thread(sendcli.send, [str(f2)])
    assert ok is False and "PIN" in msg, (ok, msg)                 # no saved PIN yet
    config.update(pin="999999")
    ok, msg = await asyncio.to_thread(sendcli.send, [str(f2)])
    assert ok is False and "wrong PIN" in msg, (ok, msg)
    config.update(pin=PIN)
    ok, msg = await asyncio.to_thread(sendcli.send, [str(f2)])
    assert ok is True and msg == "", (ok, msg)
    assert await pc_to_phone(phone, f2) == f2.read_bytes()
    ok, msg = await asyncio.to_thread(sendcli.send, [str(src / "nope.txt")])
    assert ok is False and "nope.txt" in msg, (ok, msg)
    ok, msg = await asyncio.to_thread(sendcli.send, [str(f2)], 1)   # nothing listens there
    assert ok is False and "not running" in msg, (ok, msg)
    exe, args = sendto.target_and_args()
    assert args.endswith("--send") and ("btbridge_gui.pyw" in args), (exe, args)
    assert sendto.supported() is False and sendto.set_enabled(True) is False        # Windows only
    print("sendcli (Send to helper): no PIN / wrong PIN / ok / missing file / not running OK")

    b.stop()
    try: await srv
    except asyncio.CancelledError: pass
    shutil.rmtree(folder, ignore_errors=True); shutil.rmtree(src, ignore_errors=True)
    print("\nALL PASSED")

asyncio.run(main())
