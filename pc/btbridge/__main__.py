import argparse
import asyncio
import secrets
import socket
import sys

from . import config, pairing, qr
from . import protocol as P


def config_path():
    return config.config_path()


def load_pin(new: bool, forced: str = None) -> str:
    if forced:
        return forced
    pin = config.load().get("pin")
    if not new and isinstance(pin, str) and pin:
        return pin
    pin = f"{secrets.randbelow(10**6):06d}"
    config.update(pin=pin)
    return pin


def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # no packet is sent; just picks the LAN interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


NEEDS_CRYPTO = ("Jisr now encrypts the connection and needs one more package.\n\n"
                "Open a command prompt (Win+R, type cmd) and run:\n\n"
                "    python -m pip install cryptography\n\n"
                "then start Jisr again.")


def check_cryptography():
    """The encrypted link needs the 'cryptography' package; say so clearly if it is missing."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        pass
    print(NEEDS_CRYPTO)
    try:                                    # the windowed launcher has no console to read the text from
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Jisr", NEEDS_CRYPTO)
    except Exception:
        pass
    sys.exit(1)


def send_to_running(paths) -> int:
    """--send: hand the files to the Jisr that is already running. Errors are shown in a message box,
    because the windowed program has no console."""
    from . import sendcli
    ok, msg = sendcli.send(paths)
    if msg:
        print(msg)
    if not ok or msg:
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            (messagebox.showwarning if ok else messagebox.showerror)("Jisr", msg)
        except Exception:
            pass
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="btbridge", description="Phone Bluetooth bridge - PC side")
    ap.add_argument("--nogui", action="store_true", help="console only")
    ap.add_argument("--new-pin", action="store_true", help="generate a new PIN")
    ap.add_argument("--pin", help="use this PIN (not saved)")
    ap.add_argument("--port", type=int, default=P.PORT)
    ap.add_argument("--pad-mode", choices=("desktop", "game"),
                    help="gamepad as mouse+keyboard (desktop) or virtual Xbox pad (game); remembered")
    ap.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    ap.add_argument("--send", nargs="+", metavar="FILE",
                    help="send these files to the connected phone (used by Explorer's Send to menu)")
    args = ap.parse_args()
    if args.send:
        sys.exit(send_to_running(args.send))
    check_cryptography()
    from .server import Bridge

    pin = load_pin(args.new_pin, args.pin)
    ips = local_ips()

    if args.pad_mode:
        config.update(pad_mode=args.pad_mode)
    pad_mode = config.load().get("pad_mode", "desktop")

    def remember_mode(mode):
        config.update(pad_mode=mode)

    if not args.nogui:
        try:
            from . import gui
            gui.run(pin, ips, args.port, pad_mode=pad_mode, on_mode=remember_mode, minimized=args.minimized)
            return
        except ImportError:
            print("tkinter not available - running in console mode")

    bridge = Bridge(pin, log=print, port=args.port, pad_mode=pad_mode, on_mode=remember_mode)
    print("=" * 44)
    print(f" PIN          : {pin}")
    print(f" PC address   : {', '.join(ips) or 'unknown'}")
    print(f" Port         : {args.port}")
    print(f" Gamepad mode : {bridge.pad_mode}   (Start+Select on the pad switches)")
    print("=" * 44)
    if ips:
        try:
            print("\nScan this with the phone app (Scan QR) to pair:\n")
            print(qr.to_text(qr.make(pairing.pair_url(ips, args.port, pin))))
            print()
        except Exception:
            pass                      # e.g. a console that cannot show these characters
    try:
        asyncio.run(bridge.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
