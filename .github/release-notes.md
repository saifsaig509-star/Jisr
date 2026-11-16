**Jisr-Setup.exe** is the Windows installer (Windows 10/11, 64-bit). Download it, run it, then open Jisr from the Start menu.

Windows may show a blue "Windows protected your PC" (SmartScreen) window because the file is not code-signed yet.
Click **More info**, then **Run anyway**. To check that the file is the one built here, compare its SHA-256:
`certutil -hashfile Jisr-Setup.exe SHA256` must print the value in `Jisr-Setup.exe.sha256`.

You also need the Jisr app on your Android phone. Both must be on the same Wi-Fi.
