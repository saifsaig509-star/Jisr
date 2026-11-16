"""Small QR Code generator (byte mode, versions 1-10, any text up to ~210 bytes) with no dependencies.

    from btbridge import qr
    modules = qr.make("btbridge://pair?...")      # list of rows, True = dark
    print(qr.to_text(modules))                    # for a console
"""

# ---- tables for versions 1..10 (index 0 unused) -------------------------------------------------
_ECC_PER_BLOCK = {
    "L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),
    "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
    "Q": (0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24),
    "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28),
}
_NUM_BLOCKS = {
    "L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),
    "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
    "Q": (0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8),
    "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8),
}
_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
MAX_VERSION = 10


def _raw_codewords(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result // 8


def _align_positions(ver):
    if ver == 1:
        return []
    n = ver // 7 + 2
    size = ver * 4 + 17
    step = (ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
    return [6] + [size - 7 - i * step for i in range(n - 2, -1, -1)]


# ---- Reed-Solomon over GF(256) -----------------------------------------------------------------
def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 2)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---- encoding ----------------------------------------------------------------------------------
def _data_codewords(payload: bytes, ver: int, ecc: str):
    capacity = _raw_codewords(ver) - _ECC_PER_BLOCK[ecc][ver] * _NUM_BLOCKS[ecc][ver]
    bits = []

    def put(value, length):
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)                                  # byte mode
    put(len(payload), 8 if ver <= 9 else 16)
    for b in payload:
        put(b, 8)
    if len(bits) > capacity * 8:
        return None
    bits.extend([0] * min(4, capacity * 8 - len(bits)))      # terminator
    bits.extend([0] * (-len(bits) % 8))
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < capacity:
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    return data


def _interleave(data, ver, ecc):
    n_blocks = _NUM_BLOCKS[ecc][ver]
    ecc_len = _ECC_PER_BLOCK[ecc][ver]
    raw = _raw_codewords(ver)
    n_short = n_blocks - raw % n_blocks
    short_len = raw // n_blocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(n_blocks):
        chunk = data[k:k + short_len - ecc_len + (0 if i < n_short else 1)]
        k += len(chunk)
        e = _rs_remainder(chunk, divisor)
        if i < n_short:
            chunk = chunk + [0]                      # placeholder so every block has the same length
        blocks.append(chunk + e)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= n_short:
                out.append(blk[i])
    return out


class _Matrix:
    def __init__(self, ver, ecc):
        self.ver, self.ecc = ver, ecc
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()

    def _set(self, x, y, dark):
        self.mod[y][x] = dark
        self.func[y][x] = True

    def _draw_function_patterns(self):
        n = self.size
        for i in range(n):                                       # timing
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):          # finders + separators
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _align_positions(self.ver)
        for i, cy in enumerate(pos):                             # alignment patterns
            for j, cx in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format(0)
        self._draw_version()

    def _draw_format(self, mask):
        data = (_FORMAT_BITS[self.ecc] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        bit = lambda i: ((bits >> i) & 1) != 0
        n = self.size
        for i in range(0, 6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(0, 8):
            self._set(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, bit(i))
        self._set(8, n - 8, True)                                # the always-dark module

    def _draw_version(self):
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (self.ver << 12) | rem
        for i in range(18):
            b = ((bits >> i) & 1) != 0
            a = self.size - 11 + i % 3
            c = i // 3
            self._set(a, c, b)
            self._set(c, a, b)

    def draw_codewords(self, data):
        n = self.size
        i = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = (n - 1 - vert) if upward else vert
                    if not self.func[y][x] and i < len(data) * 8:
                        self.mod[y][x] = ((data[i >> 3] >> (7 - (i & 7))) & 1) != 0
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        n = self.size
        for y in range(n):
            for x in range(n):
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    inv = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    inv = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    inv = ((x + y) % 2 + x * y % 3) % 2 == 0
                if inv and not self.func[y][x]:
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self):
        n, m = self.size, self.mod
        total = 0
        lines = [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
        pat_a = [True, False, True, True, True, False, True, False, False, False, False]
        pat_b = list(reversed(pat_a))
        for line in lines:
            run = 1
            for i in range(1, n):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        total += 3 + run - 5
                    run = 1
            if run >= 5:
                total += 3 + run - 5
            for i in range(n - 10):                                # finder-like patterns
                seg = line[i:i + 11]
                if seg == pat_a or seg == pat_b:
                    total += 40
        for y in range(n - 1):                                     # 2x2 blocks
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    total += 3
        dark = sum(sum(row) for row in m)
        k = (abs(dark * 20 - n * n * 10) + n * n - 1) // (n * n) - 1
        return total + max(k, 0) * 10


def make(text, ecc: str = "M"):
    """Return the QR code as a list of rows (list of bool, True = dark module), without a quiet zone."""
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    for ver in range(1, MAX_VERSION + 1):
        data = _data_codewords(payload, ver, ecc)
        if data is not None:
            break
    else:
        raise ValueError("text too long for this QR generator (max about 210 bytes)")
    m = _Matrix(ver, ecc)
    m.draw_codewords(_interleave(data, ver, ecc))
    best_mask, best_pen = 0, None
    for mask in range(8):
        m.apply_mask(mask)
        m._draw_format(mask)
        pen = m.penalty()
        m.apply_mask(mask)                                         # undo
        if best_pen is None or pen < best_pen:
            best_mask, best_pen = mask, pen
    m.apply_mask(best_mask)
    m._draw_format(best_mask)
    return m.mod


def to_text(modules, border: int = 2, invert: bool = True) -> str:
    """Render with half-block characters (two module rows per text line) for a console.

    invert=True suits the usual light-text-on-dark console: the block characters then draw the LIGHT
    modules, so the code looks right (dark modules on a light background) to a phone camera.
    """
    n = len(modules)
    size = n + 2 * border

    def dark(x, y):
        x -= border
        y -= border
        return 0 <= x < n and 0 <= y < n and modules[y][x]

    def filled(x, y):
        return dark(x, y) != invert

    lines = []
    for y in range(0, size, 2):
        row = []
        for x in range(size):
            top, bottom = filled(x, y), filled(x, y + 1)
            row.append("\u2588" if top and bottom else "\u2580" if top else "\u2584" if bottom else " ")
        lines.append("".join(row))
    return "\n".join(lines)
