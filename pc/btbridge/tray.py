"""Optional system-tray icon (needs:  pip install pystray pillow).  Without them the program works, just without a tray icon."""
import threading

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:          # not installed
    pystray = None


def available() -> bool:
    return pystray is not None


def _image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(0, 120, 215, 255))
    # a simple Bluetooth-like rune
    pts = [(22, 22), (42, 42), (32, 52), (32, 12), (42, 22), (22, 42)]
    d.line(pts, fill=(255, 255, 255, 255), width=5, joint="curve")
    return img


class Tray:
    """on_show / on_quit are called from the tray thread: only post messages to your UI thread there."""

    def __init__(self, title, on_show, on_quit, on_send=None):
        items = [pystray.MenuItem("Show window", lambda icon, item: on_show(), default=True)]
        if on_send:
            items.append(pystray.MenuItem("Send files to phone...", lambda icon, item: on_send()))
        items.append(pystray.MenuItem("Quit", lambda icon, item: on_quit()))
        menu = pystray.Menu(*items)
        self.icon = pystray.Icon("Jisr", _image(), title, menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def set_title(self, text):
        try:
            self.icon.title = text
        except Exception:
            pass

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
