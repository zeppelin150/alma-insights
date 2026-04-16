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
APP_ID = "AlmaInsights"


def _read_version(source_dir):
    """Read version from app/src/__init__.py (single source of truth)."""
    for candidate in [
        source_dir / "app" / "src" / "__init__.py",  # installed layout
        source_dir / "src" / "__init__.py",           # raw source layout
    ]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                if line.startswith("VERSION"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return "1.0.0"  # fallback


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


def print_header(version):
    print()
    print("=" * 56)
    print(f"  {APP_NAME} -- Express Installation")
    print(f"  Version {version}")
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


def install_embedding_model(install_dir):
    """Copy bundled embedding model to data/models/ with progress."""
    src_models = install_dir / "app" / "data" / "models"
    if not src_models.exists():
        print("  [INFO] No bundled embedding model found — will download on first use")
        return

    model_dirs = [d for d in src_models.iterdir() if d.is_dir()]
    if not model_dirs:
        return

    total_size = sum(
        f.stat().st_size for d in model_dirs for f in d.rglob("*") if f.is_file()
    )
    print(f"  Embedding model: {total_size / (1024*1024):.0f} MB")


def cleanup_old_models(install_dir):
    """Remove legacy MiniLM model files if present."""
    models_dir = install_dir / "app" / "data" / "models"
    if not models_dir.exists():
        return

    legacy_name = "all-MiniLM-L6-v2"
    for d in list(models_dir.iterdir()):
        if d.is_dir() and legacy_name in d.name:
            print(f"  Cleaning up legacy model: {d.name}")
            shutil.rmtree(d, ignore_errors=True)


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

    # Copy Node.js runtime (for Gemini bridge)
    src_node = source_dir / "node"
    if src_node.exists():
        copy_with_progress(src_node, install_dir / "node", "Copying Node.js runtime")
    else:
        print("  [INFO] node/ not found — Gemini CLI must be configured manually")

    # Restore data backup if we had one
    if 'data_backup' in dir() and data_backup and data_backup.exists():
        dest_data = install_dir / "app" / "data"
        if data_backup.exists():
            shutil.copytree(data_backup, dest_data, dirs_exist_ok=True)
            shutil.rmtree(data_backup, ignore_errors=True)
            print("  Restored existing database")

    # Ensure data directory exists
    (install_dir / "app" / "data").mkdir(exist_ok=True)

    # Install/update embedding model
    install_embedding_model(install_dir)

    # Clean up legacy MiniLM model
    cleanup_old_models(install_dir)

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
        node_dir = install_dir / "node"

        launcher.write_text(
            f'@echo off\r\n'
            f'set "PATH={node_dir};%PATH%"\r\n'
            f'start "" "{python_exe}" "{main_py}"\r\n'
        )
    else:
        launcher = install_dir / "AlmaInsights.command"
        python_exe = install_dir / "python" / "bin" / "python3"
        main_py = install_dir / "app" / "main.py"
        node_bin = install_dir / "node" / "bin"

        launcher.write_text(
            f'#!/bin/bash\n'
            f'export PATH="{node_bin}:$PATH"\n'
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


def run_migrations(install_dir):
    """Apply pending database schema migrations."""
    print("  Running schema migrations...", end="", flush=True)
    system = platform.system()

    if system == "Windows":
        python_exe = install_dir / "python" / "python.exe"
    else:
        python_exe = install_dir / "python" / "bin" / "python3"

    app_dir = install_dir / "app"
    migrations_dir = app_dir / "migrations"

    if not migrations_dir.exists() or not any(migrations_dir.glob("*.sql")):
        print(" [SKIP] No migration files found")
        return

    result = subprocess.run(
        [
            str(python_exe), "-c",
            "import sys; sys.path.insert(0, '.'); "
            "from src.updater.schema_migrator import SchemaMigrator; "
            "m = SchemaMigrator(); applied = m.apply_pending(); "
            "print(f'{applied}')"
        ],
        cwd=str(app_dir),
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode == 0:
        count = result.stdout.strip().split("\n")[-1]
        print(f" [OK] {count} migration(s) applied")
    else:
        print(" [WARN] Will apply on first launch")
        if result.stderr:
            print(f"        {result.stderr[:200]}")


def migrate_settings(install_dir):
    """Migrate settings from config/ to data/ if needed (P0 migration)."""
    app_dir = install_dir / "app"
    data_settings = app_dir / "data" / "settings.yaml"
    config_settings = app_dir / "config" / "settings.yaml"

    if data_settings.exists():
        return  # Already migrated

    if config_settings.exists():
        print("  Migrating settings to data directory...", end="", flush=True)
        (app_dir / "data").mkdir(exist_ok=True)
        shutil.copy2(config_settings, data_settings)
        # Leave breadcrumb (matches runtime migration in settings_manager.py)
        migrated_marker = app_dir / "config" / "settings.yaml.migrated"
        if not migrated_marker.exists():
            config_settings.rename(migrated_marker)
        print(" [OK]")


def setup_gemini_cli(install_dir):
    """Verify or install the Gemini CLI using the bundled Node.js."""
    print()
    print("  Setting up Gemini CLI...", flush=True)

    system = platform.system()
    node_dir = install_dir / "node"

    # Find bundled node
    if system == "Windows":
        node_exe = node_dir / "node.exe"
        npm_cmd = node_dir / "npm.cmd"
        if not npm_cmd.exists():
            npm_cmd = node_dir / "npm"
        gemini_candidates = [
            node_dir / "gemini.cmd",
            node_dir / "node_modules" / ".bin" / "gemini.cmd",
            node_dir / "bin" / "gemini.cmd",
        ]
    else:
        node_exe = node_dir / "bin" / "node"
        npm_cmd = node_dir / "bin" / "npm"
        gemini_candidates = [
            node_dir / "bin" / "gemini",
            node_dir / "lib" / "node_modules" / ".bin" / "gemini",
        ]

    if not node_exe.exists():
        print("  [WARN] Bundled Node.js not found — Gemini CLI setup skipped")
        print("         Configure the Gemini CLI path manually in Settings.")
        return None

    # Check if Gemini CLI already installed in bundled node
    gemini_path = None
    for candidate in gemini_candidates:
        if candidate.exists():
            gemini_path = candidate
            break

    if gemini_path:
        print(f"  Gemini CLI found: {gemini_path.name}")
    else:
        # Install via bundled npm
        print("  Installing Gemini CLI via npm...", flush=True)
        if system == "Windows":
            cmd = ["cmd.exe", "/c", str(npm_cmd), "install", "-g",
                   "@google/gemini-cli", "--prefix", str(node_dir)]
        else:
            cmd = [str(npm_cmd), "install", "-g",
                   "@google/gemini-cli", "--prefix", str(node_dir)]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                print("  npm install complete")
                # Re-check for the binary
                for candidate in gemini_candidates:
                    if candidate.exists():
                        gemini_path = candidate
                        break
            else:
                print("  [WARN] npm install failed — configure manually in Settings")
                if result.stderr:
                    print(f"         {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            print("  [WARN] npm install timed out")
        except Exception as e:
            print(f"  [WARN] npm install error: {e}")

    if gemini_path:
        print(f"  [OK] Gemini CLI ready")

    return gemini_path


def write_gemini_settings(install_dir, gemini_path):
    """Write the Gemini CLI path into data/settings.yaml."""
    if not gemini_path:
        return

    app_dir = install_dir / "app"
    settings_file = app_dir / "data" / "settings.yaml"

    if not settings_file.exists():
        # Copy template from config if available
        template = app_dir / "config" / "settings.yaml"
        if template.exists():
            (app_dir / "data").mkdir(exist_ok=True)
            shutil.copy2(template, settings_file)

    if settings_file.exists():
        try:
            content = settings_file.read_text(encoding="utf-8")
            # Update the cli_path value if the key exists
            lines = content.splitlines(keepends=True)
            updated = False
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if stripped.startswith("cli_path:"):
                    indent = line[:len(line) - len(stripped)]
                    # Use forward slashes to avoid YAML escape issues on Windows
                    safe_path = str(gemini_path).replace("\\", "/")
                    lines[i] = f"{indent}cli_path: \"{safe_path}\"\n"
                    updated = True
                    break

            if updated:
                settings_file.write_text("".join(lines), encoding="utf-8")
                print("  Gemini CLI path saved to settings")
        except Exception:
            pass  # Non-critical — user can configure in Settings UI


def create_windows_shortcuts(install_dir):
    """Create Desktop and Start Menu shortcuts on Windows.

    Points to AlmaInsights.bat (not pythonw.exe directly) so that
    the bundled Node.js is on PATH when the app launches.
    """
    launcher_bat = install_dir / "AlmaInsights.bat"
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
$Shortcut.TargetPath = "{launcher_bat}"
$Shortcut.WorkingDirectory = "{working_dir}"
$Shortcut.Description = "{APP_NAME} -- RCM Issue Analysis"
$Shortcut.IconLocation = "{icon_path},0"
$Shortcut.WindowStyle = 7
$Shortcut.Save()

# Start Menu shortcut
$Shortcut2 = $WshShell.CreateShortcut("{start_lnk}")
$Shortcut2.TargetPath = "{launcher_bat}"
$Shortcut2.WorkingDirectory = "{working_dir}"
$Shortcut2.Description = "{APP_NAME} -- RCM Issue Analysis"
$Shortcut2.IconLocation = "{icon_path},0"
$Shortcut2.WindowStyle = 7
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
    node_dir = install_dir / "node"

    bat_path = desktop / f"{APP_NAME}.bat"
    bat_path.write_text(
        f'@echo off\r\n'
        f'set "PATH={node_dir};%PATH%"\r\n'
        f'start "" "{python_exe}" "{main_py}"\r\n'
    )
    print(f"  Created fallback launcher: {bat_path}")


def create_macos_app_bundle(install_dir, version=None):
    """Create a .app bundle in ~/Applications/."""
    if version is None:
        version = _read_version(install_dir)

    app_bundle = Path.home() / "Applications" / f"{APP_NAME}.app"
    contents = app_bundle / "Contents"
    macos_dir = contents / "MacOS"
    resources = contents / "Resources"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    python_exe = install_dir / "python" / "bin" / "python3"
    main_py = install_dir / "app" / "main.py"
    node_bin = install_dir / "node" / "bin"
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
    <string>{version}</string>
    <key>CFBundleShortVersionString</key>
    <string>{version}</string>
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

    # Launch script (includes bundled Node.js on PATH for Gemini bridge)
    launcher = macos_dir / "launch"
    launcher.write_text(
        f'#!/bin/bash\n'
        f'export PATH="{node_bin}:$PATH"\n'
        f'xattr -rd com.apple.quarantine "{install_dir / "python"}" 2>/dev/null\n'
        f'xattr -rd com.apple.quarantine "{install_dir / "node"}" 2>/dev/null\n'
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
        # Put bundled Node.js on PATH for Gemini bridge
        env = os.environ.copy()
        node_dir = install_dir / "node"
        if system == "Windows" and node_dir.exists():
            env["PATH"] = f"{node_dir};{env.get('PATH', '')}"
        elif node_dir.exists():
            env["PATH"] = f"{node_dir / 'bin'}:{env.get('PATH', '')}"

        if system == "Windows":
            python_exe = install_dir / "python" / "pythonw.exe"
            if not python_exe.exists():
                python_exe = install_dir / "python" / "python.exe"
            main_py = install_dir / "app" / "main.py"
            subprocess.Popen(
                [str(python_exe), str(main_py)],
                cwd=str(install_dir / "app"),
                env=env,
            )
        else:
            python_exe = install_dir / "python" / "bin" / "python3"
            main_py = install_dir / "app" / "main.py"
            subprocess.Popen(
                [str(python_exe), str(main_py)],
                cwd=str(install_dir / "app"),
                env=env,
            )


def main():
    source_dir = get_source_dir()
    version = _read_version(source_dir)
    print_header(version)

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

    # Install files (python, app, node)
    install_files(source_dir, install_dir)

    # Create launcher with absolute paths (includes Node.js on PATH)
    create_launcher_script(install_dir)

    # Initialize database
    init_database(install_dir)

    # Run schema migrations
    run_migrations(install_dir)

    # Migrate settings (config/ → data/) for upgrades
    migrate_settings(install_dir)

    # Set up Gemini CLI via bundled Node.js
    gemini_path = setup_gemini_cli(install_dir)
    write_gemini_settings(install_dir, gemini_path)

    # Platform-specific shortcuts
    print()
    system = platform.system()
    if system == "Windows":
        create_windows_shortcuts(install_dir)
    elif system == "Darwin":
        create_macos_app_bundle(install_dir, version=version)
    else:
        print("  [INFO] Create a shortcut manually to:")
        print(f"    {install_dir / 'AlmaInsights.command'}")

    # Done!
    print()
    print("=" * 56)
    print(f"  {APP_NAME} v{version} installation complete!")
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
