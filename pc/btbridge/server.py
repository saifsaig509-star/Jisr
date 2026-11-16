"""The PC side of the bridge: TCP server for the phone, UDP discovery, local JSON API."""
import asyncio
import hmac
import json
import os
import socket
import time
from pathlib import Path

from . import config
from . import protocol as P
from . import secure as S
from .audio import LoopbackCapture, audio_available
from .clipboard import Clipboard, MAX_CHARS as CLIP_MAX
from .files import FileTransfers
from .gamepad import Gamepad
from .inject import Injector
from .padmouse import PadMouse, BTN_START, BTN_SELECT

IDLE_TIMEOUT = 20          # seconds without any frame from the phone -> drop
AUTH_TIMEOUT = 10
MAX_FAILS = 5              # wrong PINs from one IP before a temporary ban
BAN_SECONDS = 60
# commands a local program may send to the phone through the local API
PAD_MODES = ("desktop", "game")
LOCAL_COMMANDS = {"ble_scan", "ble_stop_scan", "ble_connect", "ble_disconnect",
                  "ble_subscribe", "ble_read", "ble_write"}


class SppChannel:
    """Serial (RFCOMM) device on the phone, exposed as a TCP port on 127.0.0.1."""

    def __init__(self, session, channel, name):
        self.session, self.channel, self.name = session, channel, name
        self.server = None
        self.client = None
        self.port = 0

    async def start(self):
        for port in (P.SPP_BASE_PORT + self.channel, 0):
            try:
                self.server = await asyncio.start_server(self._on_client, "127.0.0.1", port)
                break
            except OSError:
                continue
        if self.server is None:
            raise OSError("cannot open a local port for the serial channel")
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def _on_client(self, reader, writer):
        if self.client is not None:          # one program at a time
            writer.close()
            return
        self.client = writer
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await self.session.send(P.T_SPP, bytes([self.channel & 0xFF]) + data)
        except (ConnectionError, OSError):
            pass
        finally:
            self.client = None
            writer.close()

    def from_phone(self, data: bytes):
        if self.client is not None:
            try:
                self.client.write(data)
            except Exception:
                pass

    async def close(self):
        if self.client is not None:
            self.client.close()
        if self.server is not None:
            self.server.close()


class Session:
    def __init__(self, bridge, reader, writer, phone_name, ip, chan):
        self.bridge, self.reader, self.writer = bridge, reader, writer
        self.chan = chan                      # S.Channel: encrypts everything we send
        self.phone_name, self.ip = phone_name, ip
        self.lock = asyncio.Lock()
        self.closed = False
        self.spp = {}
        self.audio = None
        self.audio_task = None
        self.clip_task = None          # watches the PC clipboard while the phone asked for sync
        self.last_clip = None          # last text that crossed the link (stops echoes)
        self.files = FileTransfers(self.send_json, lambda payload: self.send(P.T_FILE, payload),
                                   bridge.receive_dir, bridge.log, bridge.file_event, parity=0)

    # ---- sending --------------------------------------------------------
    async def send(self, ftype: int, payload: bytes):
        if self.closed:
            return
        try:
            async with self.lock:             # nonces are counters: seal + write must stay in order
                self.writer.write(self.chan.seal(ftype, payload))
                await self.writer.drain()
        except (ConnectionError, OSError):
            self.close()

    async def send_json(self, obj):
        await self.send(P.T_JSON, P.json_bytes(obj))

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except Exception:
                pass

    # ---- main loop ------------------------------------------------------
    async def run(self):
        b = self.bridge
        try:
            while not self.closed:
                ftype, data = await asyncio.wait_for(
                    S.read_frame(self.reader, self.chan, P.MAX_FRAME), IDLE_TIMEOUT)
                if ftype == P.T_JSON:
                    try:
                        await self.on_json(json.loads(data))
                    except Exception as e:  # a bad message must not kill the session
                        b.log(f"bad message: {e!r}")
                elif ftype == P.T_SPP and data:
                    chan = self.spp.get(data[0])
                    if chan:
                        chan.from_phone(data[1:])
                elif ftype == P.T_FILE:
                    await self.files.on_chunk(data)
        except (asyncio.IncompleteReadError, ConnectionError, OSError, asyncio.TimeoutError, ValueError):
            pass
        finally:
            await self.cleanup()

    async def cleanup(self):
        self.close()
        self.set_clip_sync(False)
        self.files.close()
        await self.stop_audio()
        for chan in list(self.spp.values()):
            await chan.close()
        self.spp.clear()
        self.bridge.injector.release_all()
        self.bridge.release_pads()
        if self.bridge.session is self:
            self.bridge.session = None
            self.bridge.log(f"Phone disconnected: {self.phone_name}")
            self.bridge.set_state(False, "Waiting for phone")

    # ---- message handling ----------------------------------------------
    async def on_json(self, m):
        t = m.get("t")
        inj = self.bridge.injector
        if t == "key":
            inj.key(int(m["code"]), bool(m["down"]))
        elif t == "text":
            inj.type_text(str(m.get("s", "")))
        elif t == "mouse_move":
            inj.move(int(m.get("dx", 0)), int(m.get("dy", 0)))
        elif t == "mouse_btn":
            inj.button(int(m["btn"]), bool(m["down"]))
        elif t == "scroll":
            inj.scroll(float(m.get("v", 0)), float(m.get("h", 0)))
        elif t == "pad_btn":
            self.bridge.pad_button(int(m["code"]), bool(m["down"]))
        elif t == "pad_axis":
            self.bridge.pad_axis(str(m["name"]), float(m["value"]))
        elif t == "ping":
            await self.send_json({"t": "pong"})
        elif t == "audio":
            if m.get("on"):
                await self.start_audio()
            else:
                await self.stop_audio()
                await self.send_json({"t": "audio_stop"})
        elif t == "clip":
            await self.clip_from_phone(str(m.get("s", "")))
        elif t == "clip_get":
            await self.clip_to_phone(force=True)
        elif t == "clip_sync":
            self.set_clip_sync(bool(m.get("on")))
        elif t and t.startswith("file_"):
            await self.files.on_json(m)
        elif t == "spp_open":
            await self.open_spp(m)
        elif t == "spp_close":
            chan = self.spp.pop(int(m["id"]), None)
            if chan:
                await chan.close()
                self.bridge.log(f"Serial channel {chan.channel} closed")
        elif t and (t.startswith("ble_") or t.startswith("dev_")):
            m["phone"] = self.phone_name
            self.bridge.broadcast_local(m)
            if t in ("ble_state", "ble_services"):
                self.bridge.log(f"BLE {m.get('addr')}: {t[4:]} {m.get('state', '')}")

    # ---- clipboard (text only) --------------------------------------------
    async def clip_from_phone(self, text: str):
        cb = self.bridge.clipboard
        loop = asyncio.get_running_loop()
        if not cb.available:
            await self.send_json({"t": "clip_error", "msg": "PC: clipboard is only supported on Windows"})
            return
        if not text or len(text) > CLIP_MAX:
            await self.send_json({"t": "clip_error", "msg": "PC: text is empty or too long"})
            return
        ok = await loop.run_in_executor(None, cb.set_text, text)
        if ok:
            self.last_clip = text
            self.bridge.log(f"Clipboard received from phone ({len(text)} characters)")
            await self.send_json({"t": "clip_ok", "n": len(text)})
        else:
            await self.send_json({"t": "clip_error", "msg": "PC: could not write to the clipboard"})

    async def clip_to_phone(self, force: bool = False):
        cb = self.bridge.clipboard
        if not cb.available:
            if force:
                await self.send_json({"t": "clip_error", "msg": "PC: clipboard is only supported on Windows"})
            return
        text = await asyncio.get_running_loop().run_in_executor(None, cb.get_text)
        if not text:
            if force:
                await self.send_json({"t": "clip_error", "msg": "PC: the clipboard holds no text"})
            return
        if len(text) > CLIP_MAX:
            if force:
                await self.send_json({"t": "clip_error", "msg": "PC: the text is too long to send"})
            return
        if not force and text == self.last_clip:
            return
        self.last_clip = text
        await self.send_json({"t": "clip", "s": text})
        self.bridge.log(f"Clipboard sent to phone ({len(text)} characters)")

    def set_clip_sync(self, on: bool):
        """Automatic PC -> phone sync: only while the phone switched it on, never stored."""
        if on and self.clip_task is None and self.bridge.clipboard.available and not self.closed:
            self.clip_task = asyncio.create_task(self._clip_watch())
            self.bridge.log("Clipboard sync PC -> phone: on")
        elif not on and self.clip_task is not None:
            self.clip_task.cancel()
            self.clip_task = None
            self.bridge.log("Clipboard sync PC -> phone: off")

    async def _clip_watch(self):
        cb = self.bridge.clipboard
        last = cb.sequence()
        while True:
            await asyncio.sleep(0.5)
            seq = cb.sequence()
            if seq != last:
                last = seq
                await self.clip_to_phone()

    # ---- audio ----------------------------------------------------------
    async def start_audio(self):
        if self.audio:
            return
        if not audio_available():
            await self.send_json({"t": "audio_error", "msg": "PC: PyAudioWPatch not installed"})
            return
        loop = asyncio.get_running_loop()
        q = asyncio.Queue(maxsize=50)

        def put(data):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(data)

        def on_chunk(data):          # PortAudio thread
            try:
                loop.call_soon_threadsafe(put, data)
            except RuntimeError:
                pass

        cap = LoopbackCapture(on_chunk)
        try:
            rate, ch = await loop.run_in_executor(None, cap.start)
        except Exception as e:
            await self.send_json({"t": "audio_error", "msg": f"PC: {e}"})
            return
        self.audio = cap
        await self.send_json({"t": "audio_start", "rate": rate, "ch": ch})
        self.audio_task = asyncio.create_task(self._audio_pump(q))
        self.bridge.log(f"Audio streaming started ({rate} Hz, {ch} ch)")

    async def _audio_pump(self, q):
        while True:
            data = await q.get()
            await self.send(P.T_AUDIO, data)

    async def stop_audio(self):
        if self.audio_task:
            self.audio_task.cancel()
            self.audio_task = None
        if self.audio:
            cap, self.audio = self.audio, None
            await asyncio.get_running_loop().run_in_executor(None, cap.stop)
            self.bridge.log("Audio streaming stopped")

    # ---- serial ---------------------------------------------------------
    async def open_spp(self, m):
        ch = int(m["id"])
        old = self.spp.pop(ch, None)
        if old:
            await old.close()
        chan = SppChannel(self, ch, m.get("name", ""))
        try:
            port = await chan.start()
        except OSError as e:
            await self.send_json({"t": "spp_close", "id": ch})
            self.bridge.log(f"Serial channel failed: {e}")
            return
        self.spp[ch] = chan
        await self.send_json({"t": "spp_port", "id": ch, "port": port})
        self.bridge.log(f"Serial '{chan.name}' -> 127.0.0.1:{port}")


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, bridge):
        self.bridge = bridge

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() == P.DISCOVERY_MAGIC:
            info = {"name": socket.gethostname(), "port": self.bridge.port, "v": P.VERSION}
            self.transport.sendto(json.dumps(info).encode(), addr)


class Bridge:
    def __init__(self, pin: str, log=print, port: int = P.PORT, pad_mode: str = "desktop", on_mode=None,
                 receive_dir=None):
        self.pin, self.port, self._log = pin, port, log
        self._receive_dir = receive_dir      # None: the folder from the settings (default Downloads\\Jisr)
        self.file_cb = None                  # optional: called as cb(event dict) for the progress display
        self.injector = Injector()
        self.clipboard = Clipboard()
        self.gamepad = Gamepad(log=self.log)
        self.padmouse = PadMouse(self.injector)
        self.pad_mode = pad_mode if pad_mode in PAD_MODES else "desktop"
        self.on_mode = on_mode       # optional: called as cb(mode) after every mode change
        self._combo = set()          # Start/Select buttons currently held
        self._combo_fired = False
        self.session = None
        self.local_clients = set()
        self.fails = {}              # ip -> (count, banned_until)
        self.state_cb = None         # optional: called as cb(connected: bool, text: str)
        self._task = None
        self._loop = None

    def log(self, msg):
        self._log(msg)

    def receive_dir(self) -> Path:
        """Where files from the phone are saved."""
        if self._receive_dir:
            return Path(self._receive_dir)
        saved = config.load().get("receive_dir")
        return Path(saved) if isinstance(saved, str) and saved else Path.home() / "Downloads" / "Jisr"

    def file_event(self, ev):
        if self.file_cb:
            try:
                self.file_cb(ev)
            except Exception:
                pass

    def set_state(self, connected, text):
        if self.state_cb:
            try:
                self.state_cb(connected, text)
            except Exception:
                pass

    # ---- gamepad routing --------------------------------------------------
    def set_pad_mode(self, mode: str):
        """Thread-safe: 'desktop' (mouse + keyboard) or 'game' (virtual Xbox 360 pad)."""
        if mode not in PAD_MODES:
            return
        if self._loop:
            self._loop.call_soon_threadsafe(self._apply_mode, mode)
        else:
            self._apply_mode(mode)

    def _apply_mode(self, mode: str):
        if mode == self.pad_mode:
            return
        self.release_pads()
        self.pad_mode = mode
        self.log("Gamepad mode: " + ("desktop (mouse + keyboard)" if mode == "desktop" else "game (virtual Xbox pad)"))
        if self.on_mode:
            try:
                self.on_mode(mode)
            except Exception:
                pass

    def release_pads(self):
        self.padmouse.reset()
        self.gamepad.reset()

    def pad_button(self, code: int, down: bool):
        if code in (BTN_START, BTN_SELECT):
            # Start + Select together toggle the mode; the chord itself is never forwarded
            if down:
                self._combo.add(code)
                if len(self._combo) == 2:
                    self._combo_fired = True
                    self._apply_mode("game" if self.pad_mode == "desktop" else "desktop")
                    return
            else:
                self._combo.discard(code)
                fired = self._combo_fired
                if not self._combo:
                    self._combo_fired = False
                if fired:
                    return
        if self.pad_mode == "desktop":
            self.padmouse.button(code, down)
        else:
            self.gamepad.button(code, down)

    def pad_axis(self, name: str, value: float):
        if self.pad_mode == "desktop":
            self.padmouse.axis(name, value)
        else:
            self.gamepad.axis(name, value)

    # ---- files (called from the window / tray thread) -------------------------
    def send_files(self, paths, timeout: float = 5):
        """Thread-safe: queue files for the phone. -> (how many queued, [error texts])"""
        if not self._loop:
            return 0, ["Jisr is not running"]
        fut = asyncio.run_coroutine_threadsafe(self._send_files(paths), self._loop)
        return fut.result(timeout)

    async def _send_files(self, paths):
        sess = self.session
        if sess is None or sess.closed:
            return 0, ["No phone is connected"]
        return sess.files.send_files(paths)

    def cancel_files(self):
        """Thread-safe: stop every running or waiting file transfer."""
        sess = self.session
        if self._loop and sess:
            asyncio.run_coroutine_threadsafe(sess.files.cancel_all(), self._loop)

    # ---- local API ------------------------------------------------------
    def broadcast_local(self, m):
        line = (json.dumps(m) + "\n").encode()
        for w in list(self.local_clients):
            try:
                w.write(line)
            except Exception:
                self.local_clients.discard(w)

    async def _local_client(self, reader, writer):
        self.local_clients.add(writer)
        try:
            async for line in reader:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("t") in LOCAL_COMMANDS and self.session:
                    await self.session.send_json(m)
                elif m.get("t") == "send_file":
                    await self._local_send_file(m, writer)
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            self.local_clients.discard(writer)
            writer.close()

    async def _local_send_file(self, m, writer):
        """'Send to > Jisr' in Explorer: only a program that knows the PIN (it is in the user's own
        settings folder) may ask this PC to read files and send them to the phone."""
        given = str(m.get("pin", "")).encode("utf-8")
        if not hmac.compare_digest(given, self.pin.encode("utf-8")):
            reply = {"t": "send_file_result", "ok": False, "msg": "wrong PIN (is another Jisr instance running?)"}
        else:
            paths = [str(p) for p in m.get("paths", []) if isinstance(p, str)][:200]
            queued, errors = await self._send_files(paths)
            reply = {"t": "send_file_result", "ok": queued > 0, "queued": queued,
                     "msg": "; ".join(errors) if errors else ""}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()

    # ---- phone connections ---------------------------------------------
    def _banned(self, ip):
        count, until = self.fails.get(ip, (0, 0))
        return count >= MAX_FAILS and time.time() < until

    def _fail(self, ip):
        count, _ = self.fails.get(ip, (0, 0))
        self.fails[ip] = (count + 1, time.time() + BAN_SECONDS)

    async def _handle(self, reader, writer):
        ip = writer.get_extra_info("peername")[0]
        sess = None
        try:
            if self._banned(ip):
                writer.write(P.pack_json({"t": "error", "code": "banned", "msg": "Too many wrong PINs"}))
                await writer.drain()
                return
            priv, pc_pub = S.new_keypair()
            nonce = os.urandom(16)
            writer.write(P.pack_json({"t": "challenge", "nonce": nonce.hex(), "v": P.VERSION,
                                      "pub": S.b64(pc_pub)}))
            await writer.drain()
            ftype, data = await asyncio.wait_for(P.read_frame(reader), AUTH_TIMEOUT)
            hello = json.loads(data) if ftype == P.T_JSON else {}
            if hello.get("t") != "hello" or "pub" not in hello:
                # an older phone app that does not know the encrypted protocol
                writer.write(P.pack_json({"t": "error", "code": "version",
                                          "msg": "Update the phone app (encrypted protocol)"}))
                await writer.drain()
                self.log(f"{ip} uses an old protocol - update the phone app")
                return
            phone_pub = S.unb64(str(hello["pub"]))
            pkey = S.pin_key(self.pin)
            expected = S.auth_mac(pkey, nonce, pc_pub, phone_pub)
            try:
                given = bytes.fromhex(str(hello.get("mac", "")))
            except ValueError:
                given = b""
            if not hmac.compare_digest(given, expected):
                self._fail(ip)
                self.log(f"Wrong PIN from {ip}")
                writer.write(P.pack_json({"t": "error", "code": "auth", "msg": "Wrong PIN"}))
                await writer.drain()
                return
            chan = S.server_channel(priv, phone_pub, pkey, nonce, pc_pub)
            self.fails.pop(ip, None)
            if self.session:             # a reconnecting phone replaces its stale session
                self.session.close()
            name = str(hello.get("name", "phone"))[:40]
            sess = Session(self, reader, writer, name, ip, chan)
            self.session = sess
            caps = {"inject": self.injector.available, "gamepad": self.gamepad.available,
                    "audio": audio_available(), "clipboard": self.clipboard.available, "files": True}
            await sess.send_json({"t": "hello_ok", "pc": socket.gethostname(), "caps": caps})
            self.log(f"Phone connected: {name} ({ip})")
            self.set_state(True, f"Connected: {name}")
            await sess.run()
        except (asyncio.IncompleteReadError, ConnectionError, OSError, asyncio.TimeoutError, ValueError):
            pass
        finally:
            if sess is None:
                writer.close()

    # ---- lifecycle ------------------------------------------------------
    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        server = await asyncio.start_server(self._handle, "0.0.0.0", self.port)
        local = await asyncio.start_server(self._local_client, "127.0.0.1", P.LOCAL_API_PORT)
        transport = None
        try:
            transport, _ = await self._loop.create_datagram_endpoint(
                lambda: Discovery(self), local_addr=("0.0.0.0", self.port), allow_broadcast=True)
        except Exception as e:
            self.log(f"Discovery disabled: {e}")
        self.set_state(False, "Waiting for phone")
        self.log(f"Listening on TCP {self.port}")
        pad_task = asyncio.create_task(self.padmouse.run())
        try:
            await asyncio.gather(server.serve_forever(), local.serve_forever())
        finally:
            pad_task.cancel()
            self.release_pads()
            if self.session:
                self.session.close()
            if transport:
                transport.close()
            server.close()
            local.close()

    def stop(self):
        """Thread-safe."""
        if self._loop and self._task:
            self._loop.call_soon_threadsafe(self._task.cancel)
