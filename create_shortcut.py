"""
Alma Insights — Create Desktop Shortcut
Run this ONCE:  python create_shortcut.py

After that, double-click the shortcut on your desktop to launch the app.
"""

import sys
import os
import platform
import subprocess
from pathlib import Path


APP_NAME = "Alma Insights"
APP_DIR = Path(__file__).parent.resolve()
MAIN_SCRIPT = APP_DIR / "main.py"
ICON_FILE = APP_DIR / "assets" / "alma_insights.ico"
PYTHON = sys.executable
# On Windows, use pythonw.exe (no console window) if available
PYTHONW = PYTHON.replace("python.exe", "pythonw.exe") if platform.system() == "Windows" else PYTHON
if not Path(PYTHONW).exists():
    PYTHONW = PYTHON


def create_windows_shortcut():
    """Create a .lnk shortcut on Windows desktop, with .bat fallback."""
    desktop = Path.home() / "Desktop"

    # ── Try .lnk via PowerShell (preferred — shows as proper shortcut) ──
    lnk_path = desktop / f"{APP_NAME}.lnk"
    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{lnk_path}")
$Shortcut.TargetPath = "{PYTHONW}"
$Shortcut.Arguments = '"{MAIN_SCRIPT}"'
$Shortcut.WorkingDirectory = "{APP_DIR}"
$Shortcut.Description = "{APP_NAME} — RCM Issue Analysis"
$Shortcut.IconLocation = "{ICON_FILE},0"
$Shortcut.Save()
"""
    try:
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=10
        )
        if lnk_path.exists():
            print(f"[OK] Desktop shortcut created: {lnk_path}")
            print(f"     Double-click '{APP_NAME}' on your desktop to launch.")
            return True
    except Exception:
        pass

    # ── Fallback: .bat file (always works) ──
    bat_path = desktop / f"{APP_NAME}.bat"
    bat_content = f"""@echo off
cd /d "{APP_DIR}"
start "" "{PYTHONW}" "{MAIN_SCRIPT}"
"""
    bat_path.write_text(bat_content)
    print(f"[OK] Desktop shortcut created: {bat_path}")
    print(f"     Double-click '{APP_NAME}' on your desktop to launch.")

    # Also create a hidden .vbs wrapper so the .bat doesn't flash a console window
    vbs_path = desktop / f"{APP_NAME}.vbs"
    vbs_lines = [
        'Set WshShell = CreateObject("WScript.Shell")',
        f'WshShell.CurrentDirectory = "{APP_DIR}"',
        f'WshShell.Run Chr(34) & "{PYTHONW}" & Chr(34) & " " & Chr(34) & "{MAIN_SCRIPT}" & Chr(34), 0, False',
    ]
    vbs_content = "\r\n".join(vbs_lines) + "\r\n"
    try:
        vbs_path.write_text(vbs_content)
        # If VBS works, remove the bat
        if vbs_path.exists():
            bat_path.unlink(missing_ok=True)
            print(f"[OK] Upgraded to silent launcher: {vbs_path}")
    except Exception:
        pass

    return True


def create_mac_app_bundle():
    """Create a proper .app bundle on macOS desktop."""
    desktop = Path.home() / "Desktop"
    app_bundle = desktop / f"{APP_NAME}.app"
    contents = app_bundle / "Contents"
    macos = contents / "MacOS"

    macos.mkdir(parents=True, exist_ok=True)

    # ── Info.plist ──
    plist = contents / "Info.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.alma.insights</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>launch</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
""")

    # ── Launch script ──
    launcher = macos / "launch"
    launcher.write_text(f"""#!/bin/bash
cd "{APP_DIR}"
"{PYTHON}" "{MAIN_SCRIPT}"
""")
    os.chmod(launcher, 0o755)

    print(f"[OK] Mac app bundle created: {app_bundle}")
    print(f"     Double-click '{APP_NAME}' on your desktop to launch.")
    print()
    print("     NOTE: On first launch, macOS may block it. If so:")
    print("       1. Right-click the app → 'Open'")
    print("       2. Click 'Open' in the dialog")
    print("       3. After that, it will always open normally.")
    return True


def create_linux_shortcut():
    """Create a .desktop launcher on Linux."""
    desktop = Path.home() / "Desktop"
    desktop.mkdir(exist_ok=True)

    desktop_file = desktop / "alma-insights.desktop"
    desktop_file.write_text(f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=RCM Issue Analysis & AI-Assisted Reporting
Exec={PYTHON} {MAIN_SCRIPT}
Path={APP_DIR}
Terminal=false
Categories=Office;Development;
""")
    os.chmod(desktop_file, 0o755)

    print(f"[OK] Desktop launcher created: {desktop_file}")
    print(f"     Double-click '{APP_NAME}' on your desktop to launch.")
    return True


def main():
    print()
    print(f"Creating desktop shortcut for {APP_NAME}...")
    print(f"  App location: {APP_DIR}")
    print(f"  Python:       {PYTHON}")
    print()

    system = platform.system()
    if system == "Windows":
        create_windows_shortcut()
    elif system == "Darwin":
        create_mac_app_bundle()
    else:
        create_linux_shortcut()

    print()
    input("Press Enter to close.")


if __name__ == "__main__":
    main()
