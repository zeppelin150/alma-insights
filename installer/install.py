"""
Alma Insights — Express Installer
Cross-platform console installer bundled inside the distribution zip.

Launched by:
  Windows:  Install Alma Insights.bat
  macOS:    Install Alma Insights.command

This script is run by the BUNDLED Python (not the system Python),
so it has no external dependencies beyond the standard library.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "Alma Insights"
APP_VERSION = "1.0.0"
APP_ID = "AlmaInsights"


def get_source_dir():
    """Get the distribution root (where this script's parent _installer/ lives)."""
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    return Path(__file__).resolve().parent.parent


def get_default_install_dir():
    """Platform-appropriate default install location (user-space, no admin)."""
    system = platform.system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / APP_ID
        return Path.home() / "AppData" / "Local" / APP_ID
    elif system == "Darwin":
        return Path.home() / "Applications" / APP_ID
    else:
        return Path.home() / f".{APP_ID.lower()}"


def print_header():
    print()
    print("=" * 56)
    print(f"  {APP_NAME} -- Express Installation")
    print(f"  Version {APP_VERSION}")
    print("=" * 56)
    print()


def prompt_install_dir():
    """Ask user for install location."""
    default = get_default_install_dir()

    print(f"  Install location:")
    print(f"    [1] Default: {default}")
    print(f"    [2] Choose a custom path")
    print()

    while True:
        try:
            choice = input("  Choice [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Installation cancelled.")
            sys.exit(0)

        if choice in ("", "1"):
            return default

        if choice == "2":
            try:
                custom = input("  Enter path: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Installation cancelled.")
                sys.exit(0)
            if custom:
                return Path(custom) / APP_ID
            print("  No path entered, using default.")
            return default

        print("  Please enter 1 or 2.")


def copy_with_progress(src_dir, dest_dir, label):
    """Copy a directory tree with a simple progress indicator."""
    # Count files first
    files = [f for f in src_dir.rglob("*") if f.is_file()]
    total = len(files)
    if total == 0:
        return

    bar_width = 30
    print(f"  {label}...", flush=True)

    for i, src_file in enumerate(files, 1):
        rel = src_file.relative_to(src_dir)
        dst_file = dest_dir / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)

        # Progress bar
        pct = i / total
        filled = int(bar_width * pct)
        bar = "#" * filled + "-" * (bar_width - filled)
        print(f"\r  [{bar}] {int(pct * 100):3d}%", end="", flush=True)

    print()  # newline after progress


def install_files(source_dir, install_dir):
    """Copy python/ and app/ from the distribution to the install directory."""
    print()
    print(f"  Installing to: {install_dir}")
    print()

    # Clean existing installation if present
    if install_dir.exists():
        print(f"  Existing installation found. Updating...")
        # Keep the data directory (user's database)
        data_backup = None
        existing_data = install_dir / "app" / "data"
        if existing_data.exists() and any(existing_data.iterdir()):
            data_backup = install_dir.parent / f".{APP_ID}_data_backup"
            if data_backup.exists():
                shutil.rmtree(data_backup, ignore_errors=True)
            shutil.copytree(existing_data, data_backup)

        shutil.rmtree(install_dir, ignore_errors=True)

    install_dir.mkdir(parents=True, exist_ok=True)

    # Copy Python runtime
    src_python = source_dir / "python"
    if src_python.exists():
        copy_with_progress(src_python, install_dir / "python", "Copying Python runtime")
    else:
        print("  [ERROR] python/ directory not found in distribution!")
        sys.exit(1)

    # Copy application
    src_app = source_dir / "app"
    if src_app.exists():
        copy_with_progress(src_app, install_dir / "app", "Copying application files")
    else:
        print("  [ERROR] app/ directory not found in distribution!")
        sys.exit(1)

    # Restore data backup if we had one
    if 'data_backup' in dir() and data_backup and data_backup.exists():
        dest_data = install_dir / "app" / "data"
        if data_backup.exists():
            shutil.copytree(data_backup, dest_data, dirs_exist_ok=True)
            shutil.rmtree(data_backup, ignore_errors=True)
            print("  Restored existing database")

    # Ensure data directory exists
    (install_dir / "app" / "data").mkdir(exist_ok=True)

    # Copy the direct launcher
    system = platform.system()
    if system == "Windows":
        launcher_src = source_dir / "AlmaInsights.bat"
        if launcher_src.exists():
            shutil.copy2(launcher_src, install_dir / "AlmaInsights.bat")
    else:
        launcher_src = source_dir / "AlmaInsights.command"
        if launcher_src.exists():
            shutil.copy2(launcher_src, install_dir / "AlmaInsights.command")
            os.chmod(install_dir / "AlmaInsights.command", 0o755)

    print()
    print("  [OK] Files installed")


def create_launcher_script(install_dir):
    """Create/update the launcher scripts to point to the install directory."""
    system = platform.system()

    if system == "Windows":
        # Create a launcher .bat that uses absolute paths
        launcher = install_dir / "AlmaInsights.bat"
        python_exe = install_dir / "python" / "pythonw.exe"
        if not python_exe.exists():
            python_exe = install_dir / "python" / "python.exe"
        main_py = install_dir / "app" / "main.py"

        launcher.write_text(
            f'@echo off\r\n'
            f'start "" "{python_exe}" "{main_py}"\r\n'
        )
    else:
        launcher = install_dir / "AlmaInsights.command"
        python_exe = install_dir / "python" / "bin" / "python3"
        main_py = install_dir / "app" / "main.py"

        launcher.write_text(
            f'#!/bin/bash\n'
            f'cd "$(dirname "$0")/app"\n'
            f'"{python_exe}" "{main_py}"\n'
        )
        os.chmod(launcher, 0o755)


def init_database(install_dir):
    """Initialize the SQLite database."""
    print("  Initializing database...", end="", flush=True)
    system = platform.system()

    if system == "Windows":
        python_exe = install_dir / "python" / "python.exe"
    else:
        python_exe = install_dir / "python" / "bin" / "python3"

    app_dir = install_dir / "app"

    result = subprocess.run(
        [
            str(python_exe), "-c",
            "import sys; sys.path.insert(0, '.'); "
            "from src.data.db_manager import DatabaseManager; "
            "db = DatabaseManager(); db.initialize(); db.close(); "
            "print('OK')"
        ],
        cwd=str(app_dir),
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode == 0 and "OK" in result.stdout:
        print(" [OK]")
    else:
        print(" [WARN] Will initialize on first launch")
        if result.stderr:
            print(f"        {result.stderr[:200]}")


def create_windows_shortcuts(install_dir):
    """Create Desktop and Start Menu shortcuts on Windows."""
    python_exe = install_dir / "python" / "pythonw.exe"
    if not python_exe.exists():
        python_exe = install_dir / "python" / "python.exe"
    main_py = install_dir / "app" / "main.py"
    icon_path = install_dir / "app" / "assets" / "alma_insights.ico"
    working_dir = install_dir / "app"

    # Desktop shortcut
    desktop = Path.home() / "Desktop"
    desktop_lnk = desktop / f"{APP_NAME}.lnk"

    # Start Menu shortcut
    start_menu = Path(os.environ.get(
        "APPDATA", str(Path.home() / "AppData" / "Roaming")
    )) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    start_lnk = start_menu / f"{APP_NAME}.lnk"

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell

# Desktop shortcut
$Shortcut = $WshShell.CreateShortcut("{desktop_lnk}")
$Shortcut.TargetPath = "{python_exe}"
$Shortcut.Arguments = '"{main_py}"'
$Shortcut.WorkingDirectory = "{working_dir}"
$Shortcut.Description = "{APP_NAME} -- RCM Issue Analysis"
$Shortcut.IconLocation = "{icon_path},0"
$Shortcut.Save()

# Start Menu shortcut
$Shortcut2 = $WshShell.CreateShortcut("{start_lnk}")
$Shortcut2.TargetPath = "{python_exe}"
$Shortcut2.Arguments = '"{main_py}"'
$Shortcut2.WorkingDirectory = "{working_dir}"
$Shortcut2.Description = "{APP_NAME} -- RCM Issue Analysis"
$Shortcut2.IconLocation = "{icon_path},0"
$Shortcut2.Save()
"""

    print("  Creating Desktop shortcut...", end="", flush=True)
    try:
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
        if desktop_lnk.exists():
            print(" [OK]")
        else:
            print(" [WARN] PowerShell shortcut creation may have failed")
            _create_windows_bat_fallback(install_dir, desktop)
    except FileNotFoundError:
        print(" [WARN] PowerShell not available")
        _create_windows_bat_fallback(install_dir, desktop)
    except Exception as e:
        print(f" [WARN] {e}")
        _create_windows_bat_fallback(install_dir, desktop)

    if start_lnk.exists():
        print("  Creating Start Menu shortcut... [OK]")
    else:
        print("  Start Menu shortcut... [SKIP] (will use Desktop shortcut)")


def _create_windows_bat_fallback(install_dir, desktop):
    """Fallback: create a .bat shortcut if PowerShell isn't available."""
    python_exe = install_dir / "python" / "pythonw.exe"
    if not python_exe.exists():
        python_exe = install_dir / "python" / "python.exe"
    main_py = install_dir / "app" / "main.py"

    bat_path = desktop / f"{APP_NAME}.bat"
    bat_path.write_text(
        f'@echo off\r\n'
        f'start "" "{python_exe}" "{main_py}"\r\n'
    )
    print(f"  Created fallback launcher: {bat_path}")


def create_macos_app_bundle(install_dir):
    """Create a .app bundle in ~/Applications/."""
    app_bundle = Path.home() / "Applications" / f"{APP_NAME}.app"
    contents = app_bundle / "Contents"
    macos_dir = contents / "MacOS"
    resources = contents / "Resources"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    python_exe = install_dir / "python" / "bin" / "python3"
    main_py = install_dir / "app" / "main.py"
    icon_src = install_dir / "app" / "assets" / "alma_insights.png"

    # Info.plist
    plist = contents / "Info.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.alma.insights</string>
    <key>CFBundleVersion</key>
    <string>{APP_VERSION}</string>
    <key>CFBundleShortVersionString</key>
    <string>{APP_VERSION}</string>
    <key>CFBundleExecutable</key>
    <string>launch</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
""")

    # Launch script
    launcher = macos_dir / "launch"
    launcher.write_text(
        f'#!/bin/bash\n'
        f'cd "{install_dir / "app"}"\n'
        f'exec "{python_exe}" "{main_py}"\n'
    )
    os.chmod(launcher, 0o755)

    # Copy icon if available
    if icon_src.exists():
        shutil.copy2(icon_src, resources / "app.png")

    print(f"  Creating app bundle... [OK]")
    print(f"    {app_bundle}")

    # Also create a desktop alias (symlink)
    desktop = Path.home() / "Desktop"
    desktop_link = desktop / f"{APP_NAME}.app"
    if not desktop_link.exists():
        try:
            desktop_link.symlink_to(app_bundle)
            print(f"  Desktop alias created")
        except OSError:
            print(f"  Desktop alias... [SKIP]")


def offer_launch(install_dir):
    """Ask user if they want to launch the app."""
    system = platform.system()
    print()

    try:
        response = input("  Launch Alma Insights now? (y/n) [y]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        response = "n"

    if response in ("", "y", "yes"):
        print("  Launching...")
        if system == "Windows":
            python_exe = install_dir / "python" / "pythonw.exe"
            if not python_exe.exists():
                python_exe = install_dir / "python" / "python.exe"
            main_py = install_dir / "app" / "main.py"
            subprocess.Popen(
                [str(python_exe), str(main_py)],
                cwd=str(install_dir / "app"),
            )
        else:
            python_exe = install_dir / "python" / "bin" / "python3"
            main_py = install_dir / "app" / "main.py"
            subprocess.Popen(
                [str(python_exe), str(main_py)],
                cwd=str(install_dir / "app"),
            )


def main():
    print_header()

    source_dir = get_source_dir()

    # Verify we're in the right place
    if not (source_dir / "python").exists():
        print(f"  [ERROR] Cannot find 'python/' in: {source_dir}")
        print(f"  Make sure you extracted the full zip before running the installer.")
        input("\n  Press Enter to exit...")
        sys.exit(1)

    if not (source_dir / "app").exists():
        print(f"  [ERROR] Cannot find 'app/' in: {source_dir}")
        input("\n  Press Enter to exit...")
        sys.exit(1)

    # Prompt for install location
    install_dir = prompt_install_dir()

    # Install
    install_files(source_dir, install_dir)

    # Create launcher with absolute paths
    create_launcher_script(install_dir)

    # Initialize database
    init_database(install_dir)

    # Platform-specific shortcuts
    print()
    system = platform.system()
    if system == "Windows":
        create_windows_shortcuts(install_dir)
    elif system == "Darwin":
        create_macos_app_bundle(install_dir)
    else:
        print("  [INFO] Create a shortcut manually to:")
        print(f"    {install_dir / 'AlmaInsights.command'}")

    # Done!
    print()
    print("=" * 56)
    print(f"  {APP_NAME} installation complete!")
    print()
    print(f"  Installed to: {install_dir}")
    print()
    if system == "Windows":
        print(f"  Launch from:")
        print(f"    - Desktop shortcut")
        print(f"    - Start Menu > {APP_NAME}")
    elif system == "Darwin":
        print(f"  Launch from:")
        print(f"    - ~/Applications/{APP_NAME}.app")
        print(f"    - Desktop alias")
        print()
        print(f"  NOTE: On first launch macOS may ask to confirm.")
        print(f"  Right-click > Open if blocked by Gatekeeper.")
    print("=" * 56)

    offer_launch(install_dir)


if __name__ == "__main__":
    main()
