"""The text inside the pairing QR code shown by the PC and scanned by the phone app."""
import socket
from urllib.parse import quote


def pair_url(ips, port: int, pin: str, name: str = None) -> str:
    """btbridge://pair?ip=<a,b,...>&port=<n>&pin=<pin>&name=<pc name>

    All usable addresses of the PC are listed: the phone tries them in order and uses the first one it can reach.
    """
    name = name or socket.gethostname()
    return "btbridge://pair?ip=%s&port=%d&pin=%s&name=%s" % (",".join(ips[:4]), port, pin, quote(name, safe=""))
