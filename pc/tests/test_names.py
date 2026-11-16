"""The program was renamed (BT Bridge -> Jisr): old autostart / Send to entries must move to the new name.
Windows parts are exercised with fakes (this runs on any system)."""
import os, subprocess, sys, tempfile, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from btbridge import autostart, sendto

# ---- autostart: a dict-backed stand-in for the registry
reg = {}
class Key:
    def __enter__(self): return self
    def __exit__(self, *a): return False
fake = types.SimpleNamespace(
    HKEY_CURRENT_USER=0, KEY_SET_VALUE=2, REG_SZ=1,
    OpenKey=lambda root, path, res=0, acc=0: Key(),
    QueryValueEx=lambda k, name: (reg[name], 1) if name in reg else (_ for _ in ()).throw(FileNotFoundError(name)),
    SetValueEx=lambda k, name, r, t, v: reg.__setitem__(name, v),
    DeleteValue=lambda k, name: reg.pop(name) if name in reg else (_ for _ in ()).throw(FileNotFoundError(name)))
autostart.IS_WIN, autostart.winreg = True, fake

assert autostart.VALUE_NAME == "Jisr" and autostart.is_enabled() is False
reg["BTBridge"] = '"C:\\old\\BTBridge.exe" --minimized'          # written by the old version
assert autostart.is_enabled() is False                           # the new name is what counts ...
autostart.migrate()                                              # ... and start-up moves the old entry over
assert "BTBridge" not in reg and "Jisr" in reg and "--minimized" in reg["Jisr"], reg
assert autostart.is_enabled() is True
autostart.migrate(); assert list(reg) == ["Jisr"]                # nothing to do the second time
reg["BTBridge"] = "x"; assert autostart.set_enabled(True) and list(reg) == ["Jisr"]   # turning on also drops the old one
assert autostart.set_enabled(False) and reg == {} and autostart.is_enabled() is False
autostart.migrate(); assert reg == {}                            # nothing registered -> nothing invented
print("autostart: old 'BTBridge' entry migrated to 'Jisr' OK")

# ---- Send to shortcut: PowerShell is replaced by something that just creates the file
folder = os.path.join(tempfile.mkdtemp(prefix="btb_sendto_"), "Microsoft", "Windows", "SendTo")
os.makedirs(folder)
os.environ["APPDATA"] = os.path.dirname(os.path.dirname(os.path.dirname(folder)))
sendto.IS_WIN = True
made = []
def fake_run(cmd, env=None, **kw):
    assert cmd[0] == "powershell" and env["BTB_ARGS"].endswith("--send") and env["BTB_LNK"].endswith("Jisr.lnk")
    open(env["BTB_LNK"], "w").write("lnk"); made.append(env["BTB_TARGET"])
sendto.subprocess.run = fake_run
assert sendto.link_path() == os.path.join(folder, "Jisr.lnk") and not sendto.is_enabled()
open(os.path.join(folder, "BT Bridge.lnk"), "w").write("old")
sendto.migrate()
assert sorted(os.listdir(folder)) == ["Jisr.lnk"] and len(made) == 1, os.listdir(folder)
sendto.migrate(); assert len(made) == 1                           # already migrated: no second PowerShell call
open(os.path.join(folder, "BT Bridge.lnk"), "w").write("old")
assert sendto.set_enabled(True) and os.listdir(folder) == ["Jisr.lnk"]
assert sendto.set_enabled(False) and os.listdir(folder) == [] and not sendto.is_enabled()
sendto.migrate(); assert os.listdir(folder) == []
print("Send to: old 'BT Bridge.lnk' replaced by 'Jisr.lnk' OK")
print("\nALL PASSED")
