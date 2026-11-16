"""type_text: Unicode typing, Enter/Tab, Ctrl+key chords (Windows calls replaced by a recorder)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from btbridge.inject import Injector


class T(Injector):
    def __init__(self):
        super().__init__()
        self.log = []

    def _unicode(self, unit, down):
        self.log.append(("u", unit, down))

    def _key_vk(self, vk, down):
        self.log.append(("vk", vk, down))

    def _vk_for_char(self, ch):          # pretend US layout
        if ch.isalpha():
            return ord(ch.upper()), ch.isupper()
        return None


t = T()
t.type_text("ab")
assert t.log == [("u", 97, True), ("u", 97, False), ("u", 98, True), ("u", 98, False)], t.log

t.log.clear(); t.type_text("مرحبا")                    # Arabic
assert len(t.log) == 10 and t.log[0] == ("u", 0x0645, True), t.log

t.log.clear(); t.type_text("a\nb\t")
assert t.log[2:4] == [("vk", 0x0D, True), ("vk", 0x0D, False)] and t.log[-2:] == [("vk", 0x09, True), ("vk", 0x09, False)], t.log

t.log.clear(); t.type_text("a\r\nb")                    # \r\n is ONE Enter
assert [x for x in t.log if x[0] == "vk"] == [("vk", 0x0D, True), ("vk", 0x0D, False)], t.log

t.log.clear(); t.type_text("😀")                          # outside the BMP -> surrogate pair
assert [x[1] for x in t.log if x[2]] == [0xD83D, 0xDE00], t.log

t.log.clear(); t.key(113, True)                          # Ctrl held (android keycode 113)
t.log.clear(); t.type_text("c")
assert t.log == [("vk", 0x43, True), ("vk", 0x43, False)], t.log     # real C key, not a Unicode 'c'
t.log.clear(); t.type_text("C")                          # needs shift -> Shift is pressed around it
assert t.log == [("vk", 0xA0, True), ("vk", 0x43, True), ("vk", 0x43, False), ("vk", 0xA0, False)], t.log
t.log.clear(); t.type_text("é")                          # not ASCII -> still Unicode
assert t.log[0] == ("u", 0xE9, True), t.log
t.key(113, False)

t.log.clear(); t.type_text("x" * 5000)
assert len(t.log) == 2 * Injector.MAX_TEXT
print("type_text OK")
