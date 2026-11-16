"""The QR generator is checked by decoding its output with OpenCV (a different, independent implementation)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np
import cv2
from btbridge import qr


def render(mod, scale=8, border=4):
    n = len(mod)
    img = np.full(((n + 2 * border) * scale,) * 2, 255, np.uint8)
    for y in range(n):
        for x in range(n):
            if mod[y][x]:
                img[(y + border) * scale:(y + border + 1) * scale, (x + border) * scale:(x + border + 1) * scale] = 0
    return img


def decode(mod):
    text, pts, _ = cv2.QRCodeDetector().detectAndDecode(render(mod))
    return text


cases = [
    "hi",
    "btbridge://pair?ip=192.168.1.5&port=47810&pin=123456&name=PC",
    "btbridge://pair?ip=192.168.1.5,10.0.0.8,192.168.56.1&port=47810&pin=471467&name=DESKTOP-9K2LQ4A",
    "btbridge://pair?ip=192.168.1.5&port=47810&pin=000001&name=%D8%AC%D9%87%D8%A7%D8%B2",
    "x" * 60, "y" * 100, "z" * 150, "w" * 210,           # forces versions up to 10
    "".join(chr(33 + (i * 7) % 90) for i in range(130)),   # mixed characters
]
bad = 0
for t in cases:
    mod = qr.make(t)
    got = decode(mod)
    ok = got == t
    bad += not ok
    print(f"v{(len(mod) - 17) // 4:<2} {len(t.encode()):>3} bytes  {'OK ' if ok else 'FAIL'}  {t[:44]!r}")
for ecc in "LMQH":                                         # other error-correction levels
    t = "btbridge://pair?ip=192.168.1.5&port=47810&pin=123456&name=PC"
    ok = decode(qr.make(t, ecc)) == t
    bad += not ok
    print("ecc", ecc, "OK" if ok else "FAIL")
try:
    qr.make("q" * 400); print("FAIL: too long accepted"); bad += 1
except ValueError:
    print("too-long text rejected OK")

# the console rendering must scan too (both polarities): text -> pixels -> decode
def text_to_img(txt, invert, scale=6):
    rows = []
    for line in txt.splitlines():
        top = [1 if c in "\u2588\u2580" else 0 for c in line]
        bot = [1 if c in "\u2588\u2584" else 0 for c in line]
        rows += [top, bot]
    a = np.array(rows, np.uint8)
    dark = (1 - a) if invert else a                 # filled = light when inverted
    img = np.where(dark == 1, 0, 255).astype(np.uint8)
    return np.kron(img, np.ones((scale, scale), np.uint8))

t = "btbridge://pair?ip=192.168.1.5,10.0.0.8&port=47810&pin=123456&name=DESKTOP-ABC"
for inv in (True, False):
    img = text_to_img(qr.to_text(qr.make(t), border=3, invert=inv), inv)
    got = cv2.QRCodeDetector().detectAndDecode(img)[0]
    print("console text, invert =", inv, "->", "OK" if got == t else "FAIL"); bad += got != t
assert bad == 0, f"{bad} failures"
print("\nALL PASSED")
