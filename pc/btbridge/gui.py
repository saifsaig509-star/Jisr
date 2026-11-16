"""Small tkinter window: PIN, PC address, connection state, gamepad mode, autostart and a log.

With pystray installed the program also lives in the system tray: closing the window hides it there
(the bridge keeps running); use the tray menu or the Quit button to exit.
"""
import asyncio
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from . import autostart, config, files, pairing, qr, sendto, tray
from .server import Bridge

MODE_TEXT = {
    "desktop": "Desktop - gamepad controls the mouse and keyboard",
    "game": "Game - gamepad appears as a virtual Xbox 360 pad",
}


def _window_icon(root) -> None:
    """Jisr's bridge icon in the window's title bar (assets/jisr.ico; inside the packaged exe it sits next to the code)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        root.iconbitmap(os.path.join(base, "assets", "jisr.ico"))
    except Exception:
        pass                              # not Windows, or the file is missing: keep the default icon


def draw_qr(canvas, modules, target_px: int = 190, border: int = 3):
    """Draw a QR matrix on a tk Canvas (no image libraries needed)."""
    n = len(modules)
    cell = max(2, target_px // (n + 2 * border))
    total = cell * (n + 2 * border)
    canvas.config(width=total, height=total)
    for y in range(n):
        for x in range(n):
            if modules[y][x]:
                x0, y0 = (x + border) * cell, (y + border) * cell
                canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill="black", outline="")


def run(pin: str, ips, port: int, pad_mode: str = "desktop", on_mode=None, minimized: bool = False):
    q = queue.Queue()

    def mode_changed(mode):                      # called from the bridge thread
        q.put(("mode", mode))
        if on_mode:
            on_mode(mode)

    bridge = Bridge(pin, log=lambda m: q.put(("log", m)), port=port, pad_mode=pad_mode, on_mode=mode_changed)
    bridge.state_cb = lambda ok, text: q.put(("state", (ok, text)))
    bridge.file_cb = lambda ev: q.put(("file", ev))

    def runner():
        try:
            asyncio.run(bridge.run())
        except asyncio.CancelledError:
            pass
        except OSError as e:
            q.put(("log", f"Cannot start: {e}. Is Jisr already running? (check the tray icon)"))
        except Exception as e:
            q.put(("log", f"Server stopped: {e!r}"))

    threading.Thread(target=runner, daemon=True).start()

    root = tk.Tk()
    root.title("Jisr - PC")
    _window_icon(root)
    root.geometry("600x700")
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    head = ttk.Frame(frm)
    head.pack(fill="x")
    info = ttk.Frame(head)
    info.pack(side="left", fill="x", expand=True, anchor="n")
    ttk.Label(info, text="PIN (enter it on the phone)").pack(anchor="w")
    ttk.Label(info, text=pin, font=("Consolas", 28, "bold")).pack(anchor="w")
    ttk.Label(info, text="PC address: " + (", ".join(ips) or "unknown") + f"   port {port}",
              wraplength=300, justify="left").pack(anchor="w", pady=(4, 8))
    state = ttk.Label(info, text="Starting...", font=("Segoe UI", 11, "bold"), wraplength=300, justify="left")
    state.pack(anchor="w")

    # quick pairing: the phone app scans this code and fills in the address and the PIN
    if ips:
        try:
            qr_box = ttk.Frame(head)
            qr_box.pack(side="right", padx=(8, 0))
            canvas = tk.Canvas(qr_box, bg="white", highlightthickness=1, highlightbackground="#999999")
            canvas.pack()
            draw_qr(canvas, qr.make(pairing.pair_url(ips, port, pin)))
            ttk.Label(qr_box, text="Scan with the phone app").pack()
        except Exception as e:
            q.put(("log", f"QR code unavailable: {e}"))

    # --- gamepad mode
    mode_box = ttk.LabelFrame(frm, text="Gamepad mode  (Start + Select on the pad switches it)", padding=6)
    mode_box.pack(fill="x", pady=(10, 0))
    mode_var = tk.StringVar(value=bridge.pad_mode)
    for value in ("desktop", "game"):
        ttk.Radiobutton(mode_box, text=MODE_TEXT[value], value=value, variable=mode_var,
                        command=lambda: bridge.set_pad_mode(mode_var.get())).pack(anchor="w")

    # --- autostart
    autostart.migrate()
    sendto.migrate()
    auto_var = tk.BooleanVar(value=autostart.is_enabled())

    def toggle_auto():
        ok = autostart.set_enabled(auto_var.get())
        if not ok:
            auto_var.set(autostart.is_enabled())
            q.put(("log", "Could not change the autostart setting"))
        else:
            q.put(("log", "Starts with Windows: " + ("yes" if auto_var.get() else "no")))

    if autostart.supported():
        ttk.Checkbutton(frm, text="Start with Windows (in the tray)", variable=auto_var,
                        command=toggle_auto).pack(anchor="w", pady=(8, 0))

    # --- send-to menu in Explorer
    sendto_var = tk.BooleanVar(value=sendto.is_enabled())

    def toggle_sendto():
        if not sendto.set_enabled(sendto_var.get()):
            sendto_var.set(sendto.is_enabled())
            q.put(("log", "Could not change the Send to menu"))
        else:
            q.put(("log", "Explorer > right-click > Send to > Jisr: " + ("added" if sendto_var.get() else "removed")))

    if sendto.supported():
        ttk.Checkbutton(frm, text="Add \"Jisr\" to Explorer's right-click > Send to menu",
                        variable=sendto_var, command=toggle_sendto).pack(anchor="w")

    # --- files
    files_box = ttk.LabelFrame(frm, text="Files  (phone <-> PC)", padding=6)
    files_box.pack(fill="x", pady=(8, 0))
    file_btns = ttk.Frame(files_box)
    file_btns.pack(fill="x")
    bar = ttk.Progressbar(files_box, maximum=100)
    file_lbl = ttk.Label(files_box, text="", wraplength=560, justify="left")
    folder_lbl = ttk.Label(files_box, wraplength=560, justify="left", foreground="#555555")

    def show_folder():
        folder_lbl.config(text="Files from the phone are saved in:  " + str(bridge.receive_dir()))

    def send_dialog():
        paths = filedialog.askopenfilenames(parent=root, title="Choose files to send to the phone")
        if not paths:
            return

        def work():                       # asks the bridge thread, so keep the window responsive
            try:
                q.put(("sent", bridge.send_files(list(paths))))
            except Exception as e:
                q.put(("sent", (0, [str(e)])))
        threading.Thread(target=work, daemon=True).start()

    def open_folder():
        folder = bridge.receive_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(folder)      # noqa: S606 - opens Explorer
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            q.put(("log", f"Cannot open the folder: {e}"))

    def change_folder():
        chosen = filedialog.askdirectory(parent=root, title="Where should files from the phone be saved?",
                                         initialdir=str(bridge.receive_dir()))
        if chosen:
            config.update(receive_dir=chosen)
            show_folder()

    ttk.Button(file_btns, text="Send files to phone...", command=send_dialog).pack(side="left")
    ttk.Button(file_btns, text="Open received folder", command=open_folder).pack(side="left", padx=4)
    ttk.Button(file_btns, text="Change folder...", command=change_folder).pack(side="left")
    ttk.Button(file_btns, text="Cancel transfers", command=bridge.cancel_files).pack(side="right")
    bar.pack(fill="x", pady=(6, 0))
    file_lbl.pack(anchor="w")
    folder_lbl.pack(anchor="w")
    show_folder()

    def on_file_event(ev):
        arrow = "from phone" if ev["dir"] == "in" else "to phone"
        t = ev["t"]
        if t == "start":
            bar.config(value=0)
            file_lbl.config(text=f"{ev['name']}  ({files.human(ev['size'])}) {arrow}", foreground="#000000")
        elif t == "progress":
            pct = 100 * ev["done"] / ev["size"] if ev["size"] else 100
            bar.config(value=pct)
            file_lbl.config(text=f"{ev['name']}   {files.human(ev['done'])} / {files.human(ev['size'])}  "
                                 f"({pct:.0f}%)  {arrow}", foreground="#000000")
        elif t == "finished":
            bar.config(value=100)
            file_lbl.config(text=f"Done: {ev['name']}  ({files.human(ev['size'])}) {arrow}", foreground="#0a7d2c")
        elif t == "failed":
            file_lbl.config(text=f"Stopped: {ev['name']} - {ev['msg']}", foreground="#b00020")

    box = tk.Text(frm, height=8, state="disabled", wrap="word")
    box.pack(fill="both", expand=True, pady=(8, 0))

    # --- tray
    tray_icon = None

    def show_window():
        root.deiconify()
        root.lift()
        root.focus_force()

    def quit_app():
        bridge.stop()
        if tray_icon:
            tray_icon.stop()
        root.destroy()

    if tray.available():
        try:
            tray_icon = tray.Tray("Jisr", lambda: q.put(("tray", "show")), lambda: q.put(("tray", "quit")),
                                  on_send=lambda: q.put(("tray", "send")))
        except Exception as e:
            tray_icon = None
            q.put(("log", f"Tray icon unavailable: {e}"))

    ttk.Button(frm, text="Quit", command=quit_app).pack(anchor="e", pady=(8, 0))

    def poll():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == "state":
                    state.config(text=val[1], foreground="#0a7d2c" if val[0] else "#555555")
                    if tray_icon:
                        tray_icon.set_title("Jisr - " + val[1])
                elif kind == "mode":
                    mode_var.set(val)
                elif kind == "file":
                    on_file_event(val)
                elif kind == "sent":
                    queued, errors = val
                    for e in errors:
                        file_lbl.config(text=e, foreground="#b00020")
                        q.put(("log", "Not sent: " + e))
                elif kind == "tray":
                    if val == "show":
                        show_window()
                    elif val == "send":
                        send_dialog()
                    else:
                        quit_app()
                        return
                else:
                    box.config(state="normal")
                    box.insert("end", val + "\n")
                    box.see("end")
                    box.config(state="disabled")
        except queue.Empty:
            pass
        root.after(150, poll)

    def on_close():
        if tray_icon:
            root.withdraw()              # keep running in the tray
        else:
            quit_app()

    root.protocol("WM_DELETE_WINDOW", on_close)
    if minimized:
        if tray_icon:
            root.withdraw()
        else:
            root.iconify()
    poll()
    root.mainloop()
