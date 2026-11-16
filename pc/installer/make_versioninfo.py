"""Writes the Details tab of Jisr.exe (name, version, publisher) for PyInstaller's --version-file.

Usage:  python make_versioninfo.py <VERSION file> <output file>
"""
import sys
from pathlib import Path


def build(version: str) -> str:
    nums = [int(x) for x in version.strip().split(".")[:4]]
    nums += [0] * (4 - len(nums))
    tup = tuple(nums)
    dotted = ".".join(str(n) for n in tup)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Jisr'),
      StringStruct('FileDescription', 'Jisr - phone to PC bridge'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', 'Jisr'),
      StringStruct('OriginalFilename', 'Jisr.exe'),
      StringStruct('ProductName', 'Jisr'),
      StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(build(src.read_text(encoding="utf-8")), encoding="utf-8")
    print("wrote", dst)
