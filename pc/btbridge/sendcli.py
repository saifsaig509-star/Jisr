"""'Send to > Jisr' helper: asks the running Jisr to send files to the connected phone.

It talks to the local API (127.0.0.1 only) and proves it may do so with the PIN from the user's own
settings folder, so other Windows users on the same PC cannot make Jisr read your files.
"""
import json
import os
import socket

from . import config
from . import protocol as P


def send(paths, port: int = P.LOCAL_API_PORT):
    """-> (ok, message). ok is True when at least one file was queued; message lists the files that were not."""
    pin = config.load().get("pin")
    if not pin:
        return False, "Jisr has no saved PIN yet - start it once first."
    full = [os.path.abspath(p) for p in paths]
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
    except OSError:
        return False, "Jisr is not running. Start it, connect the phone, then try again."
    with s:
        s.settimeout(8)
        s.sendall((json.dumps({"t": "send_file", "pin": pin, "paths": full}) + "\n").encode("ascii"))
        buf = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return False, "Jisr closed the connection."
                buf += chunk
                while b"\n" in buf:                       # BLE events may arrive first: skip them
                    line, buf = buf.split(b"\n", 1)
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if m.get("t") == "send_file_result":
                        return bool(m.get("ok")), str(m.get("msg") or "")
        except OSError:
            return False, "Jisr did not answer."
