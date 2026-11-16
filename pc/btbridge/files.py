"""File transfer between the phone and the PC, in both directions, over the encrypted link.

Messages (JSON, T_JSON frames) - the SENDER of a file picks its id; phone ids are odd, PC ids are even,
so an id alone tells who the sender is:

  sender   -> receiver   file_offer  {id, name, size}
  receiver -> sender     file_accept {id}   |   file_reject {id, msg}
  sender   -> receiver   T_FILE frames: 4-byte big-endian id + chunk (CHUNK bytes at most)
  sender   -> receiver   file_done   {id, size, sha256}          (hex SHA-256 of everything sent)
  receiver -> sender     file_ok     {id, name, size}   |   file_error {id, msg}
  either side            file_cancel {id, msg}

The receiver writes to "<name>.part" and renames it only when size and SHA-256 match, so a broken
transfer never leaves something that looks like a finished file. File names from the other side are
reduced to a plain, safe name (no folders, no reserved Windows names) and never overwrite anything.
"""
import asyncio
import hashlib
import os
import shutil
import struct
import time
import unicodedata
from pathlib import Path

CHUNK = 64 * 1024
MAX_SIZE = 8 << 30             # 8 GiB per file
MAX_INCOMING = 4               # files received at the same time
MIN_FREE_AFTER = 64 << 20      # keep at least this much disk space free
ACCEPT_TIMEOUT = 60            # seconds the peer has to answer an offer
FINISH_TIMEOUT = 120           # ... to confirm a finished file
PROGRESS_EVERY = 0.25
ID_HEADER = struct.Struct(">I")

_BAD_CHARS = '<>:"/\\|?*'
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))     # text-direction tricks (fake extensions)
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def safe_name(name) -> str:
    """Whatever the peer sent -> one harmless file name."""
    name = str(name).replace("\\", "/").split("/")[-1]
    name = "".join("_" if (ord(c) < 32 or ord(c) == 127 or c in _BAD_CHARS) else c
                   for c in name if ord(c) not in _BIDI)
    name = name.strip().rstrip(". ")
    if not name:
        name = "file"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        ext = ext[:20]
        name = stem[:120 - len(ext)] + ext
    return unicodedata.normalize("NFC", name)


def unique_path(folder: Path, name: str) -> Path:
    """folder/name, or folder/'name (1).ext' ... - never an existing file or a transfer in progress."""
    stem, ext = os.path.splitext(name)
    for i in range(10000):
        cand = name if i == 0 else f"{stem} ({i}){ext}"
        p = folder / cand
        if not p.exists() and not (folder / (cand + ".part")).exists():
            return p
    raise OSError("too many files with the same name")


def human(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024 or unit == "GB":
            return f"{int(x)} {unit}" if unit == "B" else f"{x:.1f} {unit}"
        x /= 1024


class _In:
    def __init__(self, fid, size, final, part, f):
        self.id, self.size, self.final, self.part, self.f = fid, size, final, part, f
        self.name = final.name
        self.sha = hashlib.sha256()
        self.got = 0
        self.last = 0.0
        self.t0 = time.monotonic()


class _Out:
    def __init__(self, fid, name, size):
        self.id, self.name, self.size = fid, name, size
        self.replies = asyncio.Queue()       # what the receiver answered: (kind, text)
        self.cancelled = False
        self.reason = ""
        self.last = 0.0


class _Stop(Exception):
    """A transfer ended (cancelled / refused / failed); the message says why."""


class FileTransfers:
    """One instance per phone session. Everything runs on the event loop except the disk I/O."""

    def __init__(self, send_json, send_chunk, get_dir, log, notify=None, parity=0):
        self.send_json = send_json           # async (obj)
        self.send_chunk = send_chunk         # async (bytes)  -> one T_FILE frame
        self.get_dir = get_dir               # () -> folder for received files
        self.log = log
        self.notify = notify                 # optional (dict) for a progress display
        self.parity = parity                 # 0: this side uses even ids
        self._next = 2 if parity == 0 else 1
        self.incoming = {}
        self.outgoing = {}
        self._queue = None
        self._worker = None
        self._closed = False
        self._rx_lock = asyncio.Lock()       # a disk write / rename in a worker thread vs. the Cancel button

    # ---------------------------------------------------------------- helpers
    def _emit(self, **ev):
        if self.notify:
            try:
                self.notify(ev)
            except Exception:
                pass

    def _progress(self, direction, obj, done, size):
        now = time.monotonic()
        if now - obj.last >= PROGRESS_EVERY or done >= size:
            obj.last = now
            self._emit(t="progress", dir=direction, id=obj.id, name=obj.name, done=done, size=size)

    def _new_id(self) -> int:
        fid = self._next
        self._next = (self._next + 2) & 0x7FFFFFFF or (2 if self.parity == 0 else 1)
        return fid

    def _mine(self, fid: int) -> bool:
        return fid % 2 == self.parity

    # ---------------------------------------------------------------- sending
    def send_files(self, paths):
        """Queue files for the phone (call on the event loop). -> (how many queued, [error texts])"""
        queued, errors = 0, []
        for raw in paths:
            p = Path(raw)
            try:
                st = p.stat()
                if not p.is_file():
                    raise OSError("not a file")
                if st.st_size > MAX_SIZE:
                    raise OSError("the file is larger than 8 GB")
                with open(p, "rb"):
                    pass
            except OSError as e:
                errors.append(f"{p.name}: {e.strerror or e}")
                continue
            if self._queue is None:
                self._queue = asyncio.Queue()
            self._queue.put_nowait((p, st.st_size))
            queued += 1
        if queued and self._worker is None and not self._closed:
            self._worker = asyncio.create_task(self._worker_loop())
        return queued, errors

    async def _worker_loop(self):
        while not self._closed:
            path, size = await self._queue.get()
            try:
                await self._send_one(path, size)
            except asyncio.CancelledError:
                raise
            except Exception as e:                       # one bad file must not stop the rest
                self.log(f"File send failed ({path.name}): {e!r}")

    async def _wait(self, o, timeout):
        try:
            return await asyncio.wait_for(o.replies.get(), timeout)
        except asyncio.TimeoutError:
            raise _Stop("no answer from the phone")

    async def _send_one(self, path: Path, size: int):
        loop = asyncio.get_running_loop()
        fid = self._new_id()
        o = _Out(fid, path.name, size)
        self.outgoing[fid] = o
        f = None
        told_peer = False
        try:
            self.log(f"Sending to phone: {o.name} ({human(size)})")
            self._emit(t="start", dir="out", id=fid, name=o.name, size=size)
            await self.send_json({"t": "file_offer", "id": fid, "name": o.name, "size": size})
            kind, text = await self._wait(o, ACCEPT_TIMEOUT)
            if kind != "accept":
                told_peer = True
                raise _Stop(text or "the phone did not accept the file")
            f = await loop.run_in_executor(None, open, path, "rb")
            sha, sent = hashlib.sha256(), 0
            while sent < size:
                if o.cancelled:
                    told_peer = o.reason != "cancelled here"
                    raise _Stop(o.reason or "cancelled")
                data = await loop.run_in_executor(None, f.read, min(CHUNK, size - sent))
                if not data:
                    raise _Stop("the file changed while it was being sent")
                sha.update(data)
                await self.send_chunk(ID_HEADER.pack(fid) + data)
                sent += len(data)
                self._progress("out", o, sent, size)
            await self.send_json({"t": "file_done", "id": fid, "size": sent, "sha256": sha.hexdigest()})
            kind, text = await self._wait(o, FINISH_TIMEOUT)
            if kind != "ok":
                told_peer = True
                raise _Stop(text or "the phone reported an error")
            self.log(f"Sent to phone: {o.name}")
            self._emit(t="finished", dir="out", id=fid, name=o.name, size=size, path=text or o.name)
        except _Stop as e:
            if not told_peer and not self._closed:
                await self.send_json({"t": "file_cancel", "id": fid, "msg": str(e)})
            self.log(f"Sending {o.name} stopped: {e}")
            self._emit(t="failed", dir="out", id=fid, name=o.name, msg=str(e))
        finally:
            self.outgoing.pop(fid, None)
            if f:
                f.close()

    # ---------------------------------------------------------------- receiving
    async def on_json(self, m):
        t, fid = m.get("t"), m.get("id")
        if not isinstance(fid, int) or isinstance(fid, bool) or fid < 0:
            return
        if t == "file_offer":
            await self._on_offer(fid, m)
        elif t == "file_done":
            await self._on_done(fid, m)
        elif t == "file_cancel":
            if self._mine(fid):
                self._peer_said(fid, "cancel", str(m.get("msg") or "cancelled by the phone"))
            else:
                inc = self.incoming.get(fid)
                if inc:
                    self._drop(inc)
                    self.log(f"Receiving {inc.name} cancelled by the phone")
                    self._emit(t="failed", dir="in", id=fid, name=inc.name, msg="cancelled by the phone")
        elif t in ("file_accept", "file_reject", "file_ok", "file_error"):
            if self._mine(fid):
                kind = t[5:]
                text = str(m.get("name") if t == "file_ok" else (m.get("msg") or ""))
                self._peer_said(fid, kind, text)

    def _peer_said(self, fid, kind, text):
        o = self.outgoing.get(fid)
        if o is None:
            return
        if kind in ("reject", "error", "cancel"):
            o.cancelled = True
            o.reason = text or kind
        o.replies.put_nowait((kind, text))

    async def _reject(self, fid, why):
        await self.send_json({"t": "file_reject", "id": fid, "msg": why})

    async def _on_offer(self, fid, m):
        name, size = m.get("name"), m.get("size")
        if self._mine(fid) or fid in self.incoming:
            return await self._reject(fid, "bad file id")
        if not isinstance(name, str) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return await self._reject(fid, "bad offer")
        if size > MAX_SIZE:
            return await self._reject(fid, "the file is larger than 8 GB")
        if len(self.incoming) >= MAX_INCOMING:
            return await self._reject(fid, "too many files at once, try again in a moment")
        try:
            folder = Path(self.get_dir())
            folder.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(folder).free < size + MIN_FREE_AFTER:
                return await self._reject(fid, "PC: not enough free disk space")
            final = unique_path(folder, safe_name(name))
            part = final.with_name(final.name + ".part")
            f = open(part, "xb")
        except OSError as e:
            return await self._reject(fid, f"PC: {e.strerror or e}")
        inc = _In(fid, size, final, part, f)
        self.incoming[fid] = inc
        self.log(f"Receiving from phone: {inc.name} ({human(size)})")
        self._emit(t="start", dir="in", id=fid, name=inc.name, size=size)
        await self.send_json({"t": "file_accept", "id": fid})

    async def on_chunk(self, payload: bytes):
        if len(payload) < ID_HEADER.size:
            return
        (fid,) = ID_HEADER.unpack_from(payload)
        inc = self.incoming.get(fid)
        if inc is None:
            return                           # cancelled or unknown: ignore the rest of the stream
        data = payload[ID_HEADER.size:]
        async with self._rx_lock:
            if self.incoming.get(fid) is not inc:
                return                       # cancelled while this chunk was waiting
            inc.got += len(data)
            if inc.got > inc.size:
                return await self._fail(inc, "more data than announced")
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._write, inc, data)
            except (OSError, ValueError) as e:
                return await self._fail(inc, f"PC: {getattr(e, 'strerror', None) or e}")
            self._progress("in", inc, inc.got, inc.size)

    @staticmethod
    def _write(inc, data):
        inc.f.write(data)
        inc.sha.update(data)

    async def _on_done(self, fid, m):
        async with self._rx_lock:
            inc = self.incoming.get(fid)
            if inc is None:
                return
            if inc.got != inc.size or m.get("size") != inc.size:
                return await self._fail(inc, "the file arrived incomplete")
            if str(m.get("sha256", "")).lower() != inc.sha.hexdigest():
                return await self._fail(inc, "the file arrived damaged (checksum mismatch)")
            try:
                final = await asyncio.get_running_loop().run_in_executor(None, self._finalize, inc)
            except (OSError, ValueError) as e:
                return await self._fail(inc, f"PC: {getattr(e, 'strerror', None) or e}")
            self.incoming.pop(fid, None)
            self.log(f"Received from phone: {final}")
            self._emit(t="finished", dir="in", id=fid, name=final.name, size=inc.size, path=str(final))
            await self.send_json({"t": "file_ok", "id": fid, "name": final.name, "size": inc.size})

    @staticmethod
    def _finalize(inc) -> Path:
        inc.f.flush()
        os.fsync(inc.f.fileno())
        inc.f.close()
        target = inc.final
        if target.exists():                  # someone created that name while we were receiving
            target = unique_path(target.parent, target.name)
        last = None
        for _ in range(10):                  # antivirus may hold the new file for a moment
            try:
                os.replace(inc.part, target)
                return target
            except PermissionError as e:
                last = e
                time.sleep(0.2)
        raise last

    async def _fail(self, inc, why):
        self._drop(inc)
        self.log(f"Receiving {inc.name} failed: {why}")
        self._emit(t="failed", dir="in", id=inc.id, name=inc.name, msg=why)
        await self.send_json({"t": "file_error", "id": inc.id, "msg": why})

    def _drop(self, inc):
        """Forget an incoming file and delete what was written so far."""
        self.incoming.pop(inc.id, None)
        try:
            inc.f.close()
        except OSError:
            pass
        try:
            os.remove(inc.part)
        except OSError:
            pass

    # ---------------------------------------------------------------- control
    async def cancel_all(self):
        """The user pressed Cancel: stop everything that is running or waiting."""
        if self._queue is not None:
            while not self._queue.empty():
                self._queue.get_nowait()
        for o in list(self.outgoing.values()):
            o.cancelled, o.reason = True, "cancelled here"
            o.replies.put_nowait(("cancel", "cancelled here"))
        async with self._rx_lock:            # wait for a write that is in progress, then drop the files
            for inc in list(self.incoming.values()):
                self._drop(inc)
                self._emit(t="failed", dir="in", id=inc.id, name=inc.name, msg="cancelled here")
                await self.send_json({"t": "file_cancel", "id": inc.id, "msg": "cancelled by the PC"})

    def close(self):
        """The session ended: stop and remove every partial file."""
        self._closed = True
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
        for inc in list(self.incoming.values()):
            self._drop(inc)
            self.log(f"Receiving {inc.name} interrupted - partial file removed")
        for o in list(self.outgoing.values()):
            o.cancelled, o.reason = True, "connection closed"
            o.replies.put_nowait(("cancel", "connection closed"))
