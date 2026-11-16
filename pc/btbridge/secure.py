"""Encrypted transport for the phone <-> PC link (protocol version 2).

Handshake (the only plaintext part; after it every frame is encrypted and authenticated):

  PC    -> phone   challenge {nonce, pub}          pub  = PC's ephemeral P-256 key (X.509 DER, base64)
  phone -> PC      hello     {pub, mac, name}      mac  = HMAC(pin_key, "BTB2-auth" | nonce | pcPub | phonePub)
  PC    -> phone   hello_ok  (already encrypted)

* The two sides run ECDH on fresh (ephemeral) keys, so someone who only recorded the traffic
  cannot decrypt it afterwards, even if they later learn the PIN (forward secrecy).
* The PIN decides who may connect: pin_key = PBKDF2-HMAC-SHA256(PIN). The `mac` proves the phone
  knows the PIN and binds both public keys, so a man in the middle cannot swap them. The session
  keys also mix pin_key in, so without the PIN nobody can read or forge anything.
* Frames: 4-byte length | AES-256-GCM(type byte + payload). Each direction has its own key and a
  counter nonce, the length header is authenticated, so frames cannot be altered, replayed,
  reordered or cut short without the connection failing.

Both sides must use identical constants (the Android twin is Secure.kt).
"""
import base64
import functools
import hashlib
import hmac
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 2
PBKDF2_ITERATIONS = 20000
PIN_SALT = b"btbridge-v2-pin"
AUTH_LABEL = b"BTB2-auth"
KEY_LABEL = b"BTB2-keys"
TAG_LEN = 16
LEN_HEADER = struct.Struct(">I")


@functools.lru_cache(maxsize=8)
def pin_key(pin: str) -> bytes:
    """Slow-hashed PIN (cached: the PC checks every attempt against the same PIN)."""
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), PIN_SALT, PBKDF2_ITERATIONS, 32)


def new_keypair():
    """-> (private key object, public key as X.509 DER bytes)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    der = priv.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, der


def auth_mac(pkey: bytes, nonce: bytes, pc_pub: bytes, phone_pub: bytes) -> bytes:
    return hmac.new(pkey, AUTH_LABEL + nonce + pc_pub + phone_pub, hashlib.sha256).digest()


def _load_peer(der: bytes):
    key = serialization.load_der_public_key(der)          # also checks the point is on the curve
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("unsupported key type")
    return key


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


class Channel:
    """Encrypts/decrypts frames for one connection. Not thread-safe (use from one event loop)."""

    def __init__(self, send_key: bytes, recv_key: bytes):
        self._tx, self._rx = AESGCM(send_key), AESGCM(recv_key)
        self._tx_n = self._rx_n = 0

    @staticmethod
    def _nonce(n: int) -> bytes:
        return b"\x00\x00\x00\x00" + struct.pack(">Q", n)

    def seal(self, ftype: int, payload: bytes) -> bytes:
        """-> bytes ready for the socket: length | ciphertext+tag."""
        plain = bytes([ftype & 0xFF]) + payload
        header = LEN_HEADER.pack(len(plain) + TAG_LEN)
        nonce = self._nonce(self._tx_n)
        self._tx_n += 1
        return header + self._tx.encrypt(nonce, plain, header)

    def open(self, header: bytes, body: bytes):
        """-> (frame type, payload). Raises ValueError if the frame was altered or out of order."""
        try:
            plain = self._rx.decrypt(self._nonce(self._rx_n), body, header)
        except InvalidTag:
            raise ValueError("bad authentication tag")
        self._rx_n += 1
        return plain[0], plain[1:]


def server_channel(priv, phone_pub: bytes, pkey: bytes, nonce: bytes, pc_pub: bytes) -> Channel:
    """PC side: derive the session keys after the phone's `mac` was verified."""
    shared = priv.exchange(ec.ECDH(), _load_peer(phone_pub))
    okm = _hkdf(shared + pkey, nonce, KEY_LABEL + pc_pub + phone_pub, 64)
    return Channel(send_key=okm[32:], recv_key=okm[:32])          # first key: phone -> PC


async def read_frame(reader, chan: Channel, max_frame: int):
    """Read one encrypted frame -> (type, payload)."""
    header = await reader.readexactly(LEN_HEADER.size)
    (n,) = LEN_HEADER.unpack(header)
    if n < TAG_LEN + 1 or n > max_frame + TAG_LEN + 1:
        raise ValueError("bad frame length")
    return chan.open(header, await reader.readexactly(n))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)
