"""A minimal fake phone for the tests: plain handshake, then encrypted frames.
Written independently of server.py so it checks the protocol, not the server against itself."""
import asyncio, json, sys
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from btbridge import protocol as P
from btbridge import secure as S

PORT = 48810
PIN = "123456"


class Phone:
    """A minimal phone: plain handshake, then encrypted frames (independent of server.py)."""
    def __init__(self, r, w, chan):
        self.r, self.w, self.chan = r, w, chan

    def send(self, ftype, payload):
        self.w.write(self.chan.seal(ftype, payload))

    def send_json(self, obj):
        self.send(P.T_JSON, P.json_bytes(obj))

    def raw_frame(self, ftype, payload):          # frame bytes WITHOUT writing them (for replay tests)
        return self.chan.seal(ftype, payload)

    async def frame(self, timeout=3):
        return await asyncio.wait_for(S.read_frame(self.r, self.chan, P.MAX_FRAME), timeout)

    async def jframe(self, timeout=3):
        t, d = await self.frame(timeout)
        assert t == P.T_JSON, t
        return json.loads(d)


async def plain_json(reader, timeout=3):
    t, d = await asyncio.wait_for(P.read_frame(reader), timeout)
    assert t == P.T_JSON, t
    return json.loads(d)


async def handshake(pin=PIN, port=PORT, mac_override=None, send_pub=True):
    """-> (Phone or None, first server reply, hello message that was sent)."""
    r, w = await asyncio.open_connection("127.0.0.1", port)
    ch = await plain_json(r)
    assert ch["t"] == "challenge" and ch["v"] == P.VERSION and "pub" in ch
    nonce, pc_pub = bytes.fromhex(ch["nonce"]), S.unb64(ch["pub"])
    priv = ec.generate_private_key(ec.SECP256R1())
    my_pub = priv.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    pkey = S.pin_key(pin)
    mac = mac_override or S.auth_mac(pkey, nonce, pc_pub, my_pub)
    hello = {"t": "hello", "name": "TestPhone", "mac": mac.hex()}
    if send_pub:
        hello["pub"] = S.b64(my_pub)
    w.write(P.pack_json(hello)); await w.drain()
    # what a phone derives (mirror of server_channel)
    shared = priv.exchange(ec.ECDH(), S._load_peer(pc_pub))
    okm = S._hkdf(shared + pkey, nonce, S.KEY_LABEL + pc_pub + my_pub, 64)
    phone = Phone(r, w, S.Channel(send_key=okm[:32], recv_key=okm[32:]))
    return phone, hello, (r, w, nonce)


async def connect(pin=PIN):
    """Full connect -> (phone, reply). reply = decrypted hello_ok, or the plain error frame."""
    phone, hello, (r, w, nonce) = await handshake(pin)
    first = await asyncio.wait_for(r.readexactly(1), 3)
    if first[0] == P.T_JSON:                      # plain frame: the server refused us
        length = int.from_bytes(await r.readexactly(4), "big")
        return phone, json.loads(await r.readexactly(length))
    header = first + await r.readexactly(3)       # encrypted frame: 4-byte length first
    body = await r.readexactly(int.from_bytes(header, "big"))
    ftype, payload = phone.chan.open(header, body)
    assert ftype == P.T_JSON
    return phone, json.loads(payload)
