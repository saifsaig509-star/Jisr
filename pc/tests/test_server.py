import asyncio, json, sys, socket
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btbridge import protocol as P
from btbridge import secure as S
from btbridge.server import Bridge
from phone_client import Phone, plain_json, handshake, connect, PORT, PIN

logs = []


async def main():
    b = Bridge(PIN, log=logs.append, port=PORT)
    srv = asyncio.create_task(b.run())
    await asyncio.sleep(0.3)

    # 0. an old (v1) phone gets a clear "update" message, and it is not counted as a wrong PIN
    r0, w0 = await asyncio.open_connection("127.0.0.1", PORT)
    ch0 = await plain_json(r0)
    w0.write(P.pack_json({"t": "hello", "name": "OldPhone", "mac": "00" * 32})); await w0.drain()
    resp0 = await plain_json(r0)
    assert resp0["t"] == "error" and resp0["code"] == "version", resp0
    w0.close(); print("old protocol -> 'version' error OK")

    # 1. wrong pin
    phone, resp = await connect("000000")
    assert resp["t"] == "error" and resp["code"] == "auth", resp
    phone.w.close(); print("wrong pin rejected OK")

    # 1b. a captured hello cannot be replayed on a new connection (fresh nonce + fresh keys)
    _, hello_a, (ra, wa, _) = await handshake()
    await asyncio.wait_for(ra.read(100), 3); wa.close()
    r1, w1 = await asyncio.open_connection("127.0.0.1", PORT)
    await plain_json(r1)
    w1.write(P.pack_json(hello_a)); await w1.drain()
    resp1 = await plain_json(r1)
    assert resp1["code"] == "auth", resp1
    w1.close(); print("replayed hello rejected OK")

    # 2. right pin -> hello_ok arrives encrypted and decrypts with our derived key
    phone, resp = await connect()
    assert resp["t"] == "hello_ok", resp
    print("hello_ok:", resp)
    r, w = phone.r, phone.w

    # 3. input + ping
    for m in ({"t":"key","code":29,"down":True},{"t":"key","code":29,"down":False},
              {"t":"mouse_move","dx":5,"dy":-3},{"t":"mouse_btn","btn":1,"down":True},
              {"t":"scroll","v":-1,"h":0},{"t":"pad_btn","code":96,"down":True},
              {"t":"pad_axis","name":"lx","value":0.5},{"t":"text","s":"مرحبا abc\n"},{"t":"text"},{"t":"bogus"},{"t":"key"}):
        phone.send_json(m)
    phone.send_json({"t":"ping"}); await w.drain()
    assert (await phone.jframe())["t"] == "pong"; print("ping/pong OK (bad messages survived)")

    # 4. serial channel
    phone.send_json({"t":"spp_open","id":1,"name":"HC-05","addr":"00:11"}); await w.drain()
    sp = await phone.jframe(); assert sp["t"] == "spp_port", sp
    lr, lw = await asyncio.open_connection("127.0.0.1", sp["port"])
    lw.write(b"hello-from-pc-program"); await lw.drain()
    t, d = await phone.frame(); assert t == P.T_SPP and d[0] == 1 and d[1:] == b"hello-from-pc-program", (t, d)
    phone.send(P.T_SPP, bytes([1]) + b"AT+OK\r\n"); await w.drain()
    got = await asyncio.wait_for(lr.read(100), 3); assert got == b"AT+OK\r\n", got
    print("serial bridge both ways OK, port", sp["port"])
    lw.close()

    # 5. local API
    ar, aw = await asyncio.open_connection("127.0.0.1", P.LOCAL_API_PORT)
    await asyncio.sleep(0.1)
    phone.send_json({"t":"ble_notify","addr":"AA:BB","char":"2a37","hex":"0648"}); await w.drain()
    line = json.loads(await asyncio.wait_for(ar.readline(), 3)); assert line["t"] == "ble_notify" and line["hex"] == "0648", line
    aw.write(b'{"t":"ble_write","addr":"AA:BB","char":"ffe1","hex":"01"}\n'); await aw.drain()
    m = await phone.jframe(); assert m["t"] == "ble_write", m
    aw.write(b'{"t":"key","code":29,"down":true}\n'); await aw.drain()   # not whitelisted -> ignored
    print("local API both ways OK")

    # 6. audio unavailable here
    phone.send_json({"t":"audio","on":True}); await w.drain()
    m = await phone.jframe(); assert m["t"] == "audio_error", m; print("audio_error (expected on Linux):", m["msg"])

    # 6b. a large frame (audio-sized) round-trips through the cipher (server -> phone direction)
    big = bytes(range(256)) * 400                      # 102 400 bytes
    await b.session.send(P.T_AUDIO, big)
    t, d = await phone.frame(); assert t == P.T_AUDIO and d == big
    print("100 KB encrypted frame OK")

    # 6c. the wire bytes reveal nothing readable
    wire = phone.raw_frame(P.T_JSON, P.json_bytes({"t": "text", "s": "secret-password"}))
    assert b"secret-password" not in wire and b'"t"' not in wire
    print("wire bytes are opaque OK")

    # 7. discovery
    def disco():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2)
        s.sendto(b"BTBRIDGE?", ("127.0.0.1", PORT)); d, _ = s.recvfrom(512); return d.decode()
    print("discovery:", await asyncio.get_running_loop().run_in_executor(None, disco))

    # 8. reconnect replaces old session
    phone2, resp2 = await connect(); assert resp2["t"] == "hello_ok"
    await asyncio.sleep(0.2)
    try:
        await asyncio.wait_for(r.read(10), 2); print("old session closed OK")
    except asyncio.TimeoutError:
        print("FAIL old session still open")

    # 8b. tampering: one flipped bit and the PC drops the connection (and keeps running)
    good = phone2.raw_frame(P.T_JSON, P.json_bytes({"t": "ping"}))
    bad = bytearray(good); bad[-1] ^= 1
    phone2.w.write(bytes(bad)); await phone2.w.drain()
    await asyncio.sleep(0.3)
    assert b.session is None, "tampered frame must end the session"
    print("tampered frame -> session dropped OK")

    # 8c. replay: the same valid frame sent twice -> second one rejected
    phone3, resp3 = await connect(); assert resp3["t"] == "hello_ok"
    frame_bytes = phone3.raw_frame(P.T_JSON, P.json_bytes({"t": "ping"}))
    phone3.w.write(frame_bytes); await phone3.w.drain()
    assert (await phone3.jframe())["t"] == "pong"
    phone3.w.write(frame_bytes); await phone3.w.drain()            # replay
    await asyncio.sleep(0.3)
    assert b.session is None, "replayed frame must end the session"
    print("replayed frame -> session dropped OK")

    phone4, resp4 = await connect(); assert resp4["t"] == "hello_ok"
    phone4.w.close(); await asyncio.sleep(0.3)
    assert b.session is None; print("session cleared after disconnect OK")

    # 9. ban after repeated wrong pins
    for i in range(5):
        p5, _ = await connect("999999"); p5.w.close()
    r3, w3 = await asyncio.open_connection("127.0.0.1", PORT)
    print("after 5 failures:", await plain_json(r3))
    b.stop()
    try: await srv
    except asyncio.CancelledError: pass
    print("\nALL PASSED"); print(*logs[-6:], sep="\n")

asyncio.run(main())
