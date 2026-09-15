import os
import re
import json
import shutil
import subprocess
import threading
import urllib.request
import zipfile
import queue
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox


# ============================================================
# APPLICATION SETTINGS
# ============================================================

APP_NAME = "Palworld Server Manager"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# User-specific configuration is stored outside the application folder.
# This is especially important for packaged .exe builds, where the application
# may be installed under Program Files and should not need write permission there.
APPDATA_DIR = os.getenv("APPDATA") or os.path.join(
    os.path.expanduser("~"), "AppData", "Roaming"
)
CONFIG_DIR = os.path.join(APPDATA_DIR, "PalworldServerConfig")
CONFIG_FILE = os.path.join(CONFIG_DIR, "server_config.json")

# Previous versions stored the configuration next to server_manager.py.
# Keep this path so an existing configuration can be migrated automatically.
LEGACY_CONFIG_FILE = os.path.join(BASE_DIR, "server_config.json")

os.makedirs(CONFIG_DIR, exist_ok=True)

PALWORLD_SERVER_APP_ID = "2394010"
PALWORLD_WORKSHOP_APP_ID = "1623730"

STEAMCMD_DOWNLOAD_URL = (
    "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
)

DEFAULT_STEAMCMD_DIR = r"C:\SteamCMD"
DEFAULT_SERVER_DIR = r"C:\PalworldServer"
DEFAULT_WORKSHOP_ROOT = os.path.join(
    DEFAULT_SERVER_DIR, "steamcmd_workshop"
)


# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "steamcmd_path": "",
    "server_path": "",
    "pal_server_path": "",
    "ini_path": "",
    "mods_dir": "",
    "workshop_dir": "",
    "server_workshop_dir": "",
    "pal_mod_settings_path": "",
    "steamcmd_workshop_root": DEFAULT_WORKSHOP_ROOT,
    "server_port": "8211",
    "steam_username": "",
    "mods": [],
}


# ============================================================
# CONFIGURATION
# ============================================================

def normalize_mod_entry(mod):
    """Convert old/new mod configuration formats into one safe format."""
    if isinstance(mod, (str, int)):
        workshop_id = extract_workshop_id(str(mod))
        if not workshop_id:
            return None
        return {
            "id": workshop_id,
            "enabled": True,
            "name": "",
            "package": "",
        }

    if isinstance(mod, dict):
        workshop_id = extract_workshop_id(
            str(mod.get("id", mod.get("workshop_id", "")))
        )
        if not workshop_id:
            return None

        return {
            "id": workshop_id,
            "enabled": bool(mod.get("enabled", True)),
            "name": str(mod.get("name", "") or ""),
            "package": str(mod.get("package", "") or ""),
        }

    return None


def normalize_mod_list(mods):
    result = []
    seen = set()

    if not isinstance(mods, list):
        return result

    for mod in mods:
        normalized = normalize_mod_entry(mod)
        if not normalized:
            continue

        workshop_id = normalized["id"]
        if workshop_id in seen:
            continue

        seen.add(workshop_id)
        result.append(normalized)

    return result


def load_config():
    # If this is the first run after the storage-location change, migrate the
    # old configuration from the application directory automatically.
    if not os.path.isfile(CONFIG_FILE) and os.path.isfile(LEGACY_CONFIG_FILE):
        try:
            shutil.copy2(LEGACY_CONFIG_FILE, CONFIG_FILE)
            print(f"Migrated configuration to: {CONFIG_FILE}")
        except Exception as e:
            print(f"Could not migrate legacy configuration: {e}")

    if not os.path.isfile(CONFIG_FILE):
        return dict(DEFAULT_CONFIG)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        config = dict(DEFAULT_CONFIG)
        if isinstance(data, dict):
            config.update(data)

        # Older versions stored Workshop mods as a simple list of strings,
        # while newer versions use dictionaries. Normalize both formats.
        config["mods"] = normalize_mod_list(config.get("mods", []))

        return config

    except Exception as e:
        print(f"Could not load configuration: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(config):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temp = CONFIG_FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)

        os.replace(temp, CONFIG_FILE)
        return True

    except Exception as e:
        print(f"Could not save configuration: {e}")
        return False


# ============================================================
# GENERAL HELPERS
# ============================================================

def normalize_path(path):
    if not path:
        return ""
    return os.path.normpath(
        os.path.abspath(os.path.expandvars(str(path).strip().strip('"')))
    )


def is_valid_directory(path):
    return bool(path and os.path.isdir(path))


def is_valid_file(path):
    return bool(path and os.path.isfile(path))


def find_file(root, filename, max_depth=8):
    if not root or not os.path.isdir(root):
        return None

    root = normalize_path(root)
    try:
        root_depth = root.rstrip(os.sep).count(os.sep)

        for current_root, dirs, files in os.walk(root):
            current_depth = (
                current_root.rstrip(os.sep).count(os.sep) - root_depth
            )

            if current_depth > max_depth:
                dirs[:] = []
                continue

            for name in files:
                if name.lower() == filename.lower():
                    return os.path.join(current_root, name)

    except Exception:
        pass

    return None


def find_directory(root, directory_name, max_depth=8):
    if not root or not os.path.isdir(root):
        return None

    root = normalize_path(root)
    try:
        root_depth = root.rstrip(os.sep).count(os.sep)

        for current_root, dirs, _ in os.walk(root):
            current_depth = (
                current_root.rstrip(os.sep).count(os.sep) - root_depth
            )

            if current_depth > max_depth:
                dirs[:] = []
                continue

            for directory in dirs:
                if directory.lower() == directory_name.lower():
                    return os.path.join(current_root, directory)

    except Exception:
        pass

    return None


def safe_copytree(source, destination):
    if not os.path.isdir(source):
        return False

    os.makedirs(destination, exist_ok=True)

    for item in os.listdir(source):
        src = os.path.join(source, item)
        dst = os.path.join(destination, item)

        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    return True


def extract_workshop_id(text):
    if not text:
        return ""

    text = str(text).strip()

    # Direct numeric ID
    if re.fullmatch(r"\d{5,15}", text):
        return text

    # Steam Workshop URLs
    patterns = [
        r"/sharedfiles/filedetails/\?id=(\d{5,15})",
        r"[?&]id=(\d{5,15})",
        r"(\d{5,15})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    return ""


# ============================================================
# SERVER PATH DETECTION
# ============================================================

def detect_server_paths(server_dir):
    result = {
        "server_path": "",
        "pal_server_path": "",
        "ini_path": "",
        "mods_dir": "",
        "server_workshop_dir": "",
        "pal_mod_settings_path": "",
    }

    if not server_dir or not os.path.isdir(server_dir):
        return result

    server_dir = normalize_path(server_dir)

    pal_server = os.path.join(server_dir, "PalServer.exe")

    if os.path.isfile(pal_server):
        result["pal_server_path"] = pal_server
        result["server_path"] = server_dir
    else:
        found = find_file(server_dir, "PalServer.exe", max_depth=6)
        if found:
            result["pal_server_path"] = found
            result["server_path"] = os.path.dirname(found)

    if result["server_path"]:
        root = result["server_path"]

        result["ini_path"] = os.path.join(
            root, "Pal", "Saved", "Config",
            "WindowsServer", "PalWorldSettings.ini"
        )

        result["mods_dir"] = os.path.join(root, "Mods")

        result["server_workshop_dir"] = os.path.join(
            root, "Mods", "Workshop"
        )

        result["pal_mod_settings_path"] = os.path.join(
            root, "Mods", "PalModSettings.ini"
        )

    return result


# ============================================================
# STEAMCMD DETECTION
# ============================================================

def detect_steamcmd():
    possible_paths = [
        os.path.join(DEFAULT_STEAMCMD_DIR, "steamcmd.exe"),
        r"C:\steamcmd\steamcmd.exe",
        os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steamcmd.exe"),
        os.path.expandvars(r"%ProgramFiles%\Steam\steamcmd.exe"),
    ]

    for path in possible_paths:
        if os.path.isfile(path):
            return normalize_path(path)

    return ""


# ============================================================
# WORKSHOP PATH HANDLING
# ============================================================

def workshop_content_path_from_root(root):
    if not root:
        return ""

    root = normalize_path(root)

    candidates = [
        os.path.join(
            root, "steamapps", "workshop", "content",
            PALWORLD_WORKSHOP_APP_ID
        ),
        os.path.join(
            root, "steamcmd", "steamapps", "workshop", "content",
            PALWORLD_WORKSHOP_APP_ID
        ),
        # Also allow the user to select the content/1623730 directory itself.
        root if os.path.basename(root) == PALWORLD_WORKSHOP_APP_ID else "",
    ]

    for path in candidates:
        if path and os.path.isdir(path):
            return normalize_path(path)

    return ""


def detect_workshop_from_server(server_dir):
    if not server_dir:
        return ""

    server_dir = normalize_path(server_dir)

    candidates = [
        os.path.join(
            server_dir, "steamcmd_workshop", "steamapps",
            "workshop", "content", PALWORLD_WORKSHOP_APP_ID
        ),
        os.path.join(
            server_dir, "steamcmd_workshop", "steamcmd",
            "steamapps", "workshop", "content",
            PALWORLD_WORKSHOP_APP_ID
        ),
    ]

    for path in candidates:
        if os.path.isdir(path):
            return normalize_path(path)

    possible = find_directory(
        server_dir, PALWORLD_WORKSHOP_APP_ID, max_depth=10
    )

    if possible:
        lower = possible.lower()
        if "workshop" in lower and "content" in lower:
            return normalize_path(possible)

    return ""


def detect_normal_steam_workshop():
    possible_dirs = [
        r"C:\Program Files (x86)\Steam\steamapps\workshop\content\1623730",
        r"C:\Program Files\Steam\steamapps\workshop\content\1623730",
    ]

    for path in possible_dirs:
        if os.path.isdir(path):
            return normalize_path(path)

    roots = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
    ]

    for root in roots:
        if not os.path.isdir(root):
            continue

        found = find_directory(
            root, PALWORLD_WORKSHOP_APP_ID, max_depth=8
        )

        if found:
            lower = found.lower()
            if "workshop" in lower and "content" in lower:
                return normalize_path(found)

    return ""


def detect_workshop(server_dir="", configured_root=""):
    if server_dir:
        found = detect_workshop_from_server(server_dir)
        if found:
            return found

    found = workshop_content_path_from_root(configured_root)
    if found:
        return found

    return detect_normal_steam_workshop()


def ensure_workshop_root(root):
    root = normalize_path(root)
    if not root:
        return ""

    # SteamCMD needs a writable directory. We create the root, while the
    # actual Workshop content will be created by SteamCMD.
    os.makedirs(root, exist_ok=True)
    return root


# ============================================================
# STEAMCMD
# ============================================================

def download_steamcmd(target_dir, log_callback=None):
    def log(message):
        if log_callback:
            log_callback(message)

    target_dir = normalize_path(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    steamcmd_path = os.path.join(target_dir, "steamcmd.exe")

    if os.path.isfile(steamcmd_path):
        log(f"SteamCMD already exists: {steamcmd_path}")
        return steamcmd_path

    zip_path = os.path.join(target_dir, "steamcmd.zip")

    try:
        log("Downloading SteamCMD...")
        urllib.request.urlretrieve(
            STEAMCMD_DOWNLOAD_URL, zip_path
        )

        log("Extracting SteamCMD...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(target_dir)

        try:
            os.remove(zip_path)
        except Exception:
            pass

        if os.path.isfile(steamcmd_path):
            log("SteamCMD installation completed.")
            return steamcmd_path

        log("steamcmd.exe was not found after extraction.")
        return ""

    except Exception as e:
        log(f"SteamCMD installation failed: {e}")
        return ""


def run_steamcmd(
    steamcmd_path,
    arguments,
    log_callback=None,
    cwd=None,
    interactive=False,
):
    def log(message):
        if log_callback:
            log_callback(message)

    if not steamcmd_path or not os.path.isfile(steamcmd_path):
        log(f"SteamCMD not found: {steamcmd_path}")
        return False

    command = [steamcmd_path] + list(arguments)
    log("Running SteamCMD...")
    log(" ".join(f'"{x}"' if " " in str(x) else str(x)
                for x in command))

    try:
        if interactive:
            # Workshop downloads for Palworld may require an authenticated
            # Steam account and Steam Guard. Keep a real console available
            # so SteamCMD can ask for the password/code when necessary.
            log("Opening SteamCMD console for Workshop download...")
            process = subprocess.Popen(
                command,
                cwd=cwd or os.path.dirname(steamcmd_path),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            return_code = process.wait()

            if return_code != 0:
                log(f"SteamCMD exited with code {return_code}.")
                return False

            return True

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd or os.path.dirname(steamcmd_path),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        failed_output = False
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                if line:
                    text_line = line.rstrip()
                    log(text_line)
                    upper_line = text_line.upper()
                    if (
                        "ERROR! DOWNLOAD ITEM" in upper_line
                        or "DOWNLOAD ITEM" in upper_line and "FAILED" in upper_line
                        or "ERROR!" in upper_line
                    ):
                        failed_output = True

        return_code = process.wait()

        if return_code != 0:
            log(f"SteamCMD exited with code {return_code}.")
            return False

        if failed_output:
            log("SteamCMD reported a Workshop download failure.")
            return False

        return True

    except Exception as e:
        log(f"SteamCMD execution failed: {e}")
        return False


def _palworld_manifest_candidates(steamcmd_path, install_dir):
    """Return likely Palworld SteamCMD appmanifest locations.

    SteamCMD normally stores the Palworld manifest under the forced install
    directory, but stale manifests can also remain in SteamCMD's own
    steamapps directory after a previous interrupted update.
    """
    candidates = []

    install_dir = normalize_path(install_dir)
    steamcmd_path = normalize_path(steamcmd_path)

    direct = os.path.join(install_dir, "steamapps", f"appmanifest_{PALWORLD_SERVER_APP_ID}.acf")
    candidates.append(direct)

    steamcmd_dir = os.path.dirname(steamcmd_path)
    candidates.append(
        os.path.join(steamcmd_dir, "steamapps", f"appmanifest_{PALWORLD_SERVER_APP_ID}.acf")
    )

    # Some installations may have a nested SteamCMD directory. Only look for
    # the exact Palworld manifest, never delete/rename unrelated appmanifests.
    try:
        for root, dirs, files in os.walk(install_dir):
            depth = root[len(install_dir):].count(os.sep)
            if depth > 3:
                dirs[:] = []
                continue
            if f"appmanifest_{PALWORLD_SERVER_APP_ID}.acf" in files:
                candidates.append(os.path.join(root, f"appmanifest_{PALWORLD_SERVER_APP_ID}.acf"))
    except Exception:
        pass

    result = []
    seen = set()
    for path in candidates:
        path = normalize_path(path)
        key = os.path.normcase(path)
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            result.append(path)
    return result


def reset_palworld_app_manifest(steamcmd_path, install_dir, log_callback=None):
    """Rename stale Palworld appmanifest files so SteamCMD can recreate them.

    SteamCMD state 0x6 can persist in a damaged/stale appmanifest. Renaming
    the exact Palworld manifest is safer than deleting the whole steamapps
    directory because the latter can remove useful Steam metadata.
    """
    import time

    candidates = _palworld_manifest_candidates(steamcmd_path, install_dir)
    if not candidates:
        if log_callback:
            log_callback("No stale Palworld appmanifest_2394010.acf found to reset.")
        return False

    changed = False
    stamp = time.strftime("%Y%m%d_%H%M%S")

    for manifest in candidates:
        backup = manifest + f".backup_{stamp}"
        # Avoid overwriting an existing backup if multiple candidates happen
        # to have the same timestamp.
        counter = 1
        while os.path.exists(backup):
            backup = manifest + f".backup_{stamp}_{counter}"
            counter += 1

        try:
            os.replace(manifest, backup)
            changed = True
            if log_callback:
                log_callback(f"Reset stale SteamCMD manifest: {manifest}")
                log_callback(f"Backup created: {backup}")
        except Exception as e:
            if log_callback:
                log_callback(f"Could not reset manifest {manifest}: {e}")

    return changed


def install_palworld_server(steamcmd_path, install_dir, log_callback=None):
    if not steamcmd_path or not os.path.isfile(steamcmd_path):
        if log_callback:
            log_callback("SteamCMD executable was not found.")
        return False

    install_dir = normalize_path(install_dir)
    os.makedirs(install_dir, exist_ok=True)

    if log_callback:
        log_callback("Installing/updating Palworld Dedicated Server...")
        log_callback(f"Installation directory: {install_dir}")

    arguments = [
        "+force_install_dir", install_dir,
        "+login", "anonymous",
        "+app_update", PALWORLD_SERVER_APP_ID,
        "validate",
        "+quit",
    ]

    # First attempt is completely normal. Palworld's official documentation
    # uses this exact app_update command for the dedicated server.
    success = run_steamcmd(
        steamcmd_path, arguments, log_callback
    )

    if not success:
        # A very common Palworld-specific SteamCMD failure is:
        # Error! App '2394010' state is 0x6 after update job.
        # In that case the stale appmanifest can prevent every subsequent
        # update attempt from starting. Rename only that exact manifest and
        # retry once.
        if log_callback:
            log_callback(
                "SteamCMD update failed. Checking for a stale Palworld "
                "appmanifest_2394010.acf..."
            )

        reset_done = reset_palworld_app_manifest(
            steamcmd_path, install_dir, log_callback
        )

        if reset_done:
            if log_callback:
                log_callback("Retrying Palworld update after resetting the stale manifest...")

            success = run_steamcmd(
                steamcmd_path, arguments, log_callback
            )

        if not success:
            if log_callback:
                log_callback(
                    "Palworld update failed. The server files were not reported as updated."
            )
            return False

    pal_server = os.path.join(install_dir, "PalServer.exe")

    if os.path.isfile(pal_server):
        if log_callback:
            log_callback("Palworld Dedicated Server is ready.")
        return True

    found = find_file(install_dir, "PalServer.exe", max_depth=6)
    if found:
        if log_callback:
            log_callback(f"PalServer.exe found at: {found}")
        return True

    if log_callback:
        log_callback("PalServer.exe was not found after the update.")
    return False


# ============================================================
# WORKSHOP MOD HELPERS
# ============================================================

def find_workshop_mod(workshop_dir, workshop_id):
    if not workshop_dir or not workshop_id:
        return ""

    path = os.path.join(
        normalize_path(workshop_dir), str(workshop_id)
    )

    return path if os.path.isdir(path) else ""


def get_mod_info(mod_directory):
    if not mod_directory or not os.path.isdir(mod_directory):
        return None

    info_path = os.path.join(mod_directory, "Info.json")
    if not os.path.isfile(info_path):
        return None

    try:
        with open(info_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def get_package_name(info):
    if not isinstance(info, dict):
        return ""

    for key in (
        "PackageName", "packageName", "Package", "package"
    ):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def get_mod_display_name(info):
    if not isinstance(info, dict):
        return ""

    for key in (
        "Name", "ModName", "Title", "name"
    ):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def mod_supports_server(info):
    if not isinstance(info, dict):
        return True

    install_rule = info.get("InstallRule")

    if isinstance(install_rule, dict) and "IsServer" in install_rule:
        return bool(install_rule["IsServer"])

    return True


def get_mod_dependencies(info):
    dependencies = []

    if not isinstance(info, dict):
        return dependencies

    raw = info.get("Dependencies", info.get("Dependency"))

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    dependencies.append(value)
            elif isinstance(item, dict):
                value = get_package_name(item)
                if value:
                    dependencies.append(value)

    elif isinstance(raw, str) and raw.strip():
        dependencies.append(raw.strip())

    return dependencies


# ============================================================
# PAL MOD SETTINGS
# ============================================================

def read_pal_mod_settings(path):
    settings = {
        "global_enable": False,
        "workshop_root": "",
        "active_mods": [],
    }

    if not path or not os.path.isfile(path):
        return settings

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        match = re.search(
            r"bGlobalEnableMod\s*=\s*(true|false)",
            content, re.IGNORECASE
        )

        if match:
            settings["global_enable"] = (
                match.group(1).lower() == "true"
            )

        match = re.search(
            r"^\s*#?\s*WorkshopRootDir\s*=\s*(.+?)\s*$",
            content, re.IGNORECASE | re.MULTILINE
        )

        if match:
            settings["workshop_root"] = match.group(1).strip()

        active = re.findall(
            r"^\s*ActiveModList\s*=\s*(.+?)\s*$",
            content, re.IGNORECASE | re.MULTILINE
        )

        settings["active_mods"] = [
            x.strip() for x in active if x.strip()
        ]

    except Exception:
        pass

    return settings


def write_pal_mod_settings(path, workshop_root, active_mods):
    if not path:
        return False

    directory = os.path.dirname(normalize_path(path))
    if not directory:
        return False

    os.makedirs(directory, exist_ok=True)

    backup_path = path + ".backup"

    if os.path.isfile(path):
        try:
            shutil.copy2(path, backup_path)
        except Exception:
            pass

    lines = [
        "[PalModSettings]",
        "bGlobalEnableMod=true",
    ]

    if workshop_root:
        lines.append(f"WorkshopRootDir={workshop_root}")

    for package_name in active_mods:
        lines.append(f"ActiveModList={package_name}")

    lines.append("")

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True
    except Exception:
        return False


# ============================================================
# PALWORLD INI
# ============================================================

def parse_option_settings(content):
    match = re.search(
        r"OptionSettings\s*=\s*\((.*)\)",
        content, re.DOTALL
    )

    if not match:
        return {}

    data = match.group(1)
    pairs = {}

    pattern = (
        r'([A-Za-z0-9_]+)\s*='
        r'('
        r'"(?:[^"\\]|\\.)*"'
        r'|'
        r'\([^)]*\)'
        r'|'
        r'[^,]+'
        r')'
    )

    for match in re.finditer(pattern, data):
        pairs[match.group(1)] = match.group(2).strip()

    return pairs


def update_option_setting(content, key, new_value):
    pattern = (
        rf'({re.escape(key)}\s*=\s*)'
        r'("([^"\\]|\\.)*"|\([^)]*\)|[^,\)]+)'
    )

    if not re.search(pattern, content):
        return content, False

    new_content = re.sub(
        pattern,
        rf'\g<1>{new_value}',
        content,
        count=1
    )

    return new_content, True


def get_server_listen_port(ini_path, fallback="8211"):
    """Read the dedicated server listen port from PalWorldSettings.ini.

    Palworld's actual listening port is controlled by the startup argument
    -port=XXXX.  PublicPort is different: it is only the advertised/public
    port for community-server configuration and does not change the listen
    socket.
    """
    try:
        if ini_path and os.path.isfile(ini_path):
            with open(ini_path, "r", encoding="utf-8-sig") as f:
                content = f.read()

            settings = parse_option_settings(content)

            # Prefer an explicit ServerPort if an older/newer config contains it.
            for key in ("ServerPort", "Port", "GamePort"):
                value = settings.get(key)
                if value:
                    value = str(value).strip().strip('"')
                    if value.isdigit() and 1 <= int(value) <= 65535:
                        return value
    except Exception:
        pass

    value = str(fallback or "8211").strip().strip('"')
    if value.isdigit() and 1 <= int(value) <= 65535:
        return value
    return "8211"


# ============================================================
# MAIN APPLICATION
# ============================================================

class PalworldManagerApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.config = load_config()
        self.server_process = None
        self.server_exe_path = ""
        self.shipping_exe_path = ""
        self.setting_entries = {}
        self.log_queue = queue.Queue()
        self.busy = False
        self.server_starting = False
        self.server_stopping = False
        self.server_start_time = 0.0

        self.title(APP_NAME)
        self.geometry("1150x900")
        self.minsize(950, 700)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.build_ui()
        self.refresh_detected_paths()
        self.load_ini_settings()
        self.refresh_mod_list()

        self.after(100, self.process_log_queue)
        self.after(1000, self.monitor_server)

    # ========================================================
    # UI
    # ========================================================

    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(
            self,
            text=APP_NAME,
            font=ctk.CTkFont(size=24, weight="bold")
        )
        title.grid(row=0, column=0, padx=20, pady=10)

        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(
            row=1, column=0, sticky="nsew",
            padx=15, pady=5
        )

        self.tab_server = self.tabview.add("Server")
        self.tab_settings = self.tabview.add("Server Settings")
        self.tab_mods = self.tabview.add("Workshop Mods")
        self.tab_tools = self.tabview.add("Installation / Tools")

        self.build_server_tab()
        self.build_settings_tab()
        self.build_mods_tab()
        self.build_tools_tab()

        log_frame = ctk.CTkFrame(self)
        log_frame.grid(
            row=2, column=0, sticky="ew",
            padx=15, pady=5
        )
        log_frame.grid_columnconfigure(0, weight=1)

        self.log_textbox = ctk.CTkTextbox(
            log_frame, height=150
        )
        self.log_textbox.grid(
            row=0, column=0, sticky="ew",
            padx=8, pady=8
        )
        self.log_textbox.configure(state="disabled")

        self.log("Palworld Server Manager ready.")

    def build_server_tab(self):
        frame = self.tab_server
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text="Server Installation",
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(
            row=0, column=0, columnspan=3,
            sticky="w", padx=15, pady=10
        )

        self.server_path_entry = ctk.CTkEntry(frame)
        self.server_path_entry.grid(
            row=1, column=0, columnspan=2,
            sticky="ew", padx=15, pady=5
        )

        ctk.CTkButton(
            frame,
            text="Select Server Folder",
            command=self.select_server_folder
        ).grid(
            row=1, column=2, padx=15, pady=5
        )

        self.detect_button = ctk.CTkButton(
            frame, text="Detect Server",
            command=self.detect_server
        )
        self.detect_button.grid(
            row=2, column=0, padx=15, pady=5
        )

        self.start_button = ctk.CTkButton(
            frame,
            text="Start Server",
            fg_color="#2b8a3e",
            hover_color="#237032",
            command=self.toggle_server
        )
        self.start_button.grid(
            row=2, column=1, padx=15, pady=5
        )

        self.update_server_button = ctk.CTkButton(
            frame,
            text="Update Server",
            command=self.update_server_thread
        )
        self.update_server_button.grid(
            row=2, column=2, padx=15, pady=5
        )

        self.status_label = ctk.CTkLabel(
            frame,
            text="Status: Stopped",
            text_color="crimson",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.status_label.grid(
            row=3, column=0, columnspan=3, pady=20
        )

        self.server_info_label = ctk.CTkLabel(
            frame,
            text="",
            justify="left",
            anchor="w",
            wraplength=900
        )
        self.server_info_label.grid(
            row=4, column=0, columnspan=3,
            sticky="w", padx=15, pady=10
        )

    def build_settings_tab(self):
        frame = self.tab_settings
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(frame)
        top.grid(
            row=0, column=0, sticky="ew",
            padx=10, pady=10
        )

        self.ini_path_label = ctk.CTkLabel(
            top, text="INI: Not detected"
        )
        self.ini_path_label.pack(
            side="left", padx=10
        )

        ctk.CTkButton(
            top, text="Reload",
            width=100,
            command=self.load_ini_settings
        ).pack(side="right", padx=5)

        ctk.CTkButton(
            top, text="Save Settings",
            width=140,
            command=self.save_ini_settings
        ).pack(side="right", padx=5)

        self.settings_scroll = ctk.CTkScrollableFrame(frame)
        self.settings_scroll.grid(
            row=1, column=0, sticky="nsew",
            padx=10, pady=5
        )
        self.settings_scroll.grid_columnconfigure(1, weight=1)

    def build_mods_tab(self):
        frame = self.tab_mods
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            frame,
            text="Workshop Mod Manager",
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(
            row=0, column=0,
            sticky="w", padx=15, pady=10
        )

        paths = ctk.CTkFrame(frame)
        paths.grid(
            row=1, column=0, sticky="ew",
            padx=15, pady=5
        )
        paths.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            paths, text="Workshop Root:"
        ).grid(row=0, column=0, sticky="w",
               padx=8, pady=5)

        self.workshop_root_entry = ctk.CTkEntry(paths)
        self.workshop_root_entry.grid(
            row=0, column=1, sticky="ew",
            padx=8, pady=5
        )

        ctk.CTkButton(
            paths, text="Browse",
            width=90,
            command=self.select_workshop_root
        ).grid(row=0, column=2, padx=4)

        ctk.CTkButton(
            paths, text="Auto Detect",
            width=100,
            command=self.auto_detect_workshop
        ).grid(row=0, column=3, padx=4)

        ctk.CTkLabel(
            paths, text="Workshop Content:"
        ).grid(row=1, column=0, sticky="w",
               padx=8, pady=5)

        self.workshop_entry = ctk.CTkEntry(paths)
        self.workshop_entry.grid(
            row=1, column=1, sticky="ew",
            padx=8, pady=5
        )

        ctk.CTkButton(
            paths, text="Browse",
            width=90,
            command=self.select_workshop_folder
        ).grid(row=1, column=2, padx=4)

        ctk.CTkLabel(
            paths, text="Server Workshop:"
        ).grid(row=2, column=0, sticky="w",
               padx=8, pady=5)

        self.server_workshop_entry = ctk.CTkEntry(paths)
        self.server_workshop_entry.grid(
            row=2, column=1, sticky="ew",
            padx=8, pady=5
        )

        ctk.CTkButton(
            paths, text="Browse",
            width=90,
            command=self.select_server_workshop_folder
        ).grid(row=2, column=2, padx=4)

        ctk.CTkLabel(
            paths,
            text=(
                "Workshop Root is passed to SteamCMD with +workshop_download_item. "
                "SteamCMD stores the downloaded Palworld Workshop files below "
                "steamapps\\workshop\\content\\1623730."
            ),
            wraplength=850,
            justify="left",
            text_color="gray"
        ).grid(
            row=3, column=0, columnspan=4,
            sticky="w", padx=8, pady=8
        )

        ctk.CTkLabel(
            paths, text="Steam Username (optional):"
        ).grid(row=4, column=0, sticky="w", padx=8, pady=5)

        self.steam_username_entry = ctk.CTkEntry(
            paths, placeholder_text="Steam account username"
        )
        self.steam_username_entry.grid(
            row=4, column=1, sticky="ew", padx=8, pady=5
        )
        self.steam_username_entry.insert(
            0, self.config.get("steam_username", "")
        )

        ctk.CTkLabel(
            paths,
            text=(
                "For Palworld Workshop downloads, an account that owns "
                "Palworld may be required. Password and Steam Guard are "
                "entered directly in the SteamCMD console and are not saved."
            ),
            wraplength=850, justify="left", text_color="gray"
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=8, pady=5)

        add_frame = ctk.CTkFrame(frame)
        add_frame.grid(
            row=2, column=0, sticky="ew",
            padx=15, pady=8
        )
        add_frame.grid_columnconfigure(0, weight=1)

        self.mod_id_entry = ctk.CTkEntry(
            add_frame,
            placeholder_text=(
                "Steam Workshop URL or Workshop ID "
                "(example: 3625287786)"
            )
        )
        self.mod_id_entry.grid(
            row=0, column=0, sticky="ew",
            padx=8, pady=8
        )

        ctk.CTkButton(
            add_frame,
            text="Add Mod",
            command=self.add_mod
        ).grid(row=0, column=1, padx=8)

        self.mod_list = ctk.CTkScrollableFrame(frame)
        self.mod_list.grid(
            row=4, column=0, sticky="nsew",
            padx=15, pady=5
        )
        self.mod_list.grid_columnconfigure(1, weight=1)

        buttons = ctk.CTkFrame(frame)
        buttons.grid(
            row=5, column=0, sticky="ew",
            padx=15, pady=10
        )

        ctk.CTkButton(
            buttons,
            text="Download / Update Selected",
            command=self.download_selected_mods
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            buttons,
            text="Copy Mods to Server",
            command=self.sync_mods
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            buttons,
            text="Apply Mod Settings",
            command=self.apply_mod_settings
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            buttons,
            text="Refresh",
            command=self.refresh_mod_list
        ).pack(side="right", padx=5)

    def build_tools_tab(self):
        frame = self.tab_tools
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text="Installation and Tools",
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(
            row=0, column=0, columnspan=3,
            sticky="w", padx=15, pady=10
        )

        ctk.CTkLabel(
            frame, text="SteamCMD:"
        ).grid(row=1, column=0, sticky="w",
               padx=15, pady=8)

        self.steamcmd_entry = ctk.CTkEntry(frame)
        self.steamcmd_entry.grid(
            row=1, column=1, sticky="ew",
            padx=10, pady=8
        )

        ctk.CTkButton(
            frame, text="Browse",
            command=self.select_steamcmd
        ).grid(row=1, column=2, padx=15)

        ctk.CTkButton(
            frame,
            text="Install SteamCMD to C:\\SteamCMD",
            command=self.install_steamcmd_thread
        ).grid(
            row=2, column=0, columnspan=3,
            padx=15, pady=10
        )

        ctk.CTkLabel(
            frame,
            text="New Server Installation Folder:"
        ).grid(
            row=3, column=0, sticky="w",
            padx=15, pady=8
        )

        self.new_server_entry = ctk.CTkEntry(frame)
        self.new_server_entry.grid(
            row=3, column=1, sticky="ew",
            padx=10, pady=8
        )
        self.new_server_entry.insert(0, DEFAULT_SERVER_DIR)

        ctk.CTkButton(
            frame, text="Browse",
            command=self.select_new_server_folder
        ).grid(row=3, column=2, padx=15)

        ctk.CTkButton(
            frame,
            text="Install New Palworld Server",
            height=40,
            command=self.install_server_thread
        ).grid(
            row=4, column=0, columnspan=3,
            padx=15, pady=15
        )

        ctk.CTkButton(
            frame,
            text="Detect Existing Installation",
            command=self.detect_server
        ).grid(
            row=5, column=0, columnspan=3,
            padx=15, pady=8
        )

    # ========================================================
    # UI HELPERS
    # ========================================================

    def log(self, message):
        self.log_queue.put(str(message))

    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_textbox.configure(state="normal")
                self.log_textbox.insert("end", message + "\n")
                self.log_textbox.see("end")
                self.log_textbox.configure(state="disabled")
        except queue.Empty:
            pass

        if self.winfo_exists():
            self.after(100, self.process_log_queue)

    def run_thread(self, target):
        if self.busy:
            messagebox.showwarning(
                "Busy",
                "Another operation is already running."
            )
            return

        self.busy = True
        threading.Thread(
            target=self.thread_wrapper,
            args=(target,),
            daemon=True
        ).start()

    def thread_wrapper(self, target):
        try:
            target()
        except Exception as e:
            self.log(f"Operation failed: {e}")
        finally:
            self.busy = False

    def set_entry(self, entry, value):
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)

    # ========================================================
    # PATH REFRESH / DETECTION
    # ========================================================

    def refresh_detected_paths(self):
        steamcmd = self.config.get("steamcmd_path") or detect_steamcmd()
        if steamcmd:
            self.set_entry(self.steamcmd_entry, steamcmd)
            self.config["steamcmd_path"] = steamcmd

        server_path = self.config.get("server_path", "")
        if server_path:
            paths = detect_server_paths(server_path)
            if paths["server_path"]:
                self.apply_server_paths(paths)

        workshop_root = self.config.get(
            "steamcmd_workshop_root",
            DEFAULT_WORKSHOP_ROOT
        )
        self.set_entry(self.workshop_root_entry, workshop_root)

        workshop = self.config.get("workshop_dir", "")
        if not workshop:
            workshop = detect_workshop(
                self.config.get("server_path", ""),
                workshop_root
            )

        if workshop:
            self.set_entry(self.workshop_entry, workshop)
            self.config["workshop_dir"] = workshop

        self.set_entry(
            self.server_workshop_entry,
            self.config.get("server_workshop_dir", "")
        )

        save_config(self.config)

    def apply_server_paths(self, paths):
        self.config.update({
            "server_path": paths["server_path"],
            "pal_server_path": paths["pal_server_path"],
            "ini_path": paths["ini_path"],
            "mods_dir": paths["mods_dir"],
            "server_workshop_dir": paths["server_workshop_dir"],
            "pal_mod_settings_path": paths["pal_mod_settings_path"],
        })

        self.set_entry(
            self.server_path_entry,
            paths["server_path"]
        )

        self.server_info_label.configure(
            text=(
                f"PalServer.exe: {paths['pal_server_path']}\n"
                f"INI: {paths['ini_path']}\n"
                f"Mods: {paths['mods_dir']}\n"
                f"PalModSettings.ini: {paths['pal_mod_settings_path']}"
            )
        )

        self.ini_path_label.configure(
            text=f"INI: {paths['ini_path'] or 'Not detected'}"
        )

        save_config(self.config)

    def detect_server(self):
        path = self.server_path_entry.get().strip()

        if not path:
            path = filedialog.askdirectory(
                title="Select Palworld Server Folder"
            )

        if not path:
            return

        paths = detect_server_paths(path)

        if not paths["pal_server_path"]:
            messagebox.showerror(
                "Server not found",
                "PalServer.exe was not found in the selected folder."
            )
            return

        self.apply_server_paths(paths)

        workshop = detect_workshop(
            paths["server_path"],
            self.workshop_root_entry.get().strip()
        )

        if workshop:
            self.set_entry(self.workshop_entry, workshop)
            self.config["workshop_dir"] = workshop

        self.log("Existing Palworld server detected.")
        self.refresh_mod_list()

    def select_server_folder(self):
        path = filedialog.askdirectory(
            title="Select Palworld Server Folder"
        )
        if path:
            self.set_entry(self.server_path_entry, path)
            self.detect_server()

    def select_new_server_folder(self):
        path = filedialog.askdirectory(
            title="Select New Server Installation Folder"
        )
        if path:
            self.set_entry(self.new_server_entry, path)

    # ========================================================
    # WORKSHOP ROOT
    # ========================================================

    def select_workshop_root(self):
        path = filedialog.askdirectory(
            title="Select SteamCMD Workshop Root"
        )
        if not path:
            return

        self.set_entry(self.workshop_root_entry, path)
        self.config["steamcmd_workshop_root"] = normalize_path(path)

        content = workshop_content_path_from_root(path)
        if content:
            self.set_entry(self.workshop_entry, content)
            self.config["workshop_dir"] = content

        save_config(self.config)
        self.log(f"Workshop Root set to: {path}")
        self.refresh_mod_list()

    def auto_detect_workshop(self):
        server_dir = self.server_path_entry.get().strip()
        root = self.workshop_root_entry.get().strip()

        found = detect_workshop(server_dir, root)

        if found:
            self.set_entry(self.workshop_entry, found)
            self.config["workshop_dir"] = found
            save_config(self.config)
            self.log(f"Workshop Content detected: {found}")
            self.refresh_mod_list()
        else:
            messagebox.showinfo(
                "Workshop not found",
                "No Palworld Workshop content directory was found."
            )

    def select_workshop_folder(self):
        path = filedialog.askdirectory(
            title="Select Palworld Workshop Content Folder"
        )
        if path:
            self.set_entry(self.workshop_entry, path)
            self.config["workshop_dir"] = normalize_path(path)
            save_config(self.config)
            self.refresh_mod_list()

    def select_server_workshop_folder(self):
        path = filedialog.askdirectory(
            title="Select Server Workshop Folder"
        )
        if path:
            self.set_entry(
                self.server_workshop_entry, path
            )
            self.config["server_workshop_dir"] = normalize_path(path)
            save_config(self.config)

    # ========================================================
    # STEAMCMD TOOLS
    # ========================================================

    def select_steamcmd(self):
        path = filedialog.askopenfilename(
            title="Select steamcmd.exe",
            filetypes=[("SteamCMD", "steamcmd.exe"), ("Executable", "*.exe")]
        )

        if path:
            self.set_entry(self.steamcmd_entry, path)
            self.config["steamcmd_path"] = normalize_path(path)
            save_config(self.config)

    def install_steamcmd_thread(self):
        self.run_thread(self.install_steamcmd)

    def install_steamcmd(self):
        path = download_steamcmd(
            DEFAULT_STEAMCMD_DIR,
            self.log
        )

        if path:
            self.config["steamcmd_path"] = path
            self.after(
                0,
                lambda: self.set_entry(self.steamcmd_entry, path)
            )
            save_config(self.config)
            self.log(f"SteamCMD path saved: {path}")

    def install_server_thread(self):
        self.run_thread(self.install_server)

    def install_server(self):
        steamcmd = self.steamcmd_entry.get().strip()
        if not steamcmd:
            steamcmd = detect_steamcmd()

        if not steamcmd:
            self.log("SteamCMD not found. Install SteamCMD first.")
            return

        install_dir = self.new_server_entry.get().strip()
        if not install_dir:
            self.log("Server installation folder is empty.")
            return

        if install_palworld_server(
            steamcmd, install_dir, self.log
        ):
            self.config["steamcmd_path"] = normalize_path(steamcmd)

            paths = detect_server_paths(install_dir)
            if paths["server_path"]:
                self.after(
                    0, lambda p=paths: self.apply_server_paths(p)
                )

            root = os.path.join(
                normalize_path(install_dir),
                "steamcmd_workshop"
            )

            ensure_workshop_root(root)
            self.config["steamcmd_workshop_root"] = root

            self.after(
                0,
                lambda: self.set_entry(
                    self.workshop_root_entry, root
                )
            )

            save_config(self.config)
            self.log("Server installation completed.")

    # ========================================================
    # SERVER START / STOP / UPDATE
    # ========================================================

    def get_shipping_server_path(self, server_path):
        """Return the real Palworld server executable path."""
        if not server_path:
            return ""

        path = os.path.join(
            normalize_path(server_path),
            "Pal", "Binaries", "Win64",
            "PalServer-Win64-Shipping.exe"
        )

        if os.path.isfile(path):
            return path

        # Fallback for installations where the server executable is nested
        # differently.
        found = find_file(
            normalize_path(server_path),
            "PalServer-Win64-Shipping.exe",
            max_depth=8
        )
        return normalize_path(found) if found else ""

    def get_shipping_server_path(self, server_path):
        """Return the actual Palworld server executable path."""
        if not server_path:
            return ""

        server_path = normalize_path(server_path)
        path = os.path.join(
            server_path,
            "Pal", "Binaries", "Win64",
            "PalServer-Win64-Shipping.exe"
        )

        if os.path.isfile(path):
            return path

        found = find_file(
            server_path,
            "PalServer-Win64-Shipping.exe",
            max_depth=10
        )
        return normalize_path(found) if found else ""

    def get_shipping_process_pids(self):
        """Get every running PalServer-Win64-Shipping.exe PID.

        We deliberately use tasklist as the primary method. Windows can hide
        ExecutablePath from CIM/WMI when the manager is not elevated, while
        tasklist still provides the PID of the running image.
        """
        pids = []

        try:
            result = subprocess.run(
                [
                    "tasklist",
                    "/FI", "IMAGENAME eq PalServer-Win64-Shipping.exe",
                    "/FO", "CSV",
                    "/NH"
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )

            for line in result.stdout.splitlines():
                # CSV example:
                # "PalServer-Win64-Shipping.exe","12345","Console","1","123,456 K"
                match = re.match(
                    r'"PalServer-Win64-Shipping\.exe"\s*,\s*"(\d+)"',
                    line,
                    re.IGNORECASE
                )
                if match:
                    pid = int(match.group(1))
                    if pid not in pids:
                        pids.append(pid)
        except Exception as e:
            self.log(f"Could not query PalServer-Win64-Shipping.exe: {e}")

        return pids

    def find_shipping_server_pids(self, executable_path=None):
        """Find actual Palworld server PIDs.

        Name matching is intentional here. The server launcher can spawn the
        shipping executable in a separate process, and Windows may not expose
        its ExecutablePath to a non-elevated WMI/CIM query.
        """
        return self.get_shipping_process_pids()

    def is_shipping_server_running(self):
        return bool(self.get_shipping_process_pids())

    def is_server_port_open(self):
        """Detect a running Palworld server by its configured listen port.

        Palworld can report that the dedicated server is running before the
        process query becomes reliable. The server's listen socket is a much
        better fallback in that situation.
        """
        try:
            ini_path = self.config.get("ini_path", "")
            configured_port = self.config.get("server_port", "8211")
            port = get_server_listen_port(ini_path, configured_port)
            port = int(str(port).strip())
            if not (1 <= port <= 65535):
                return False

            # Check both TCP and UDP because Palworld's game networking is
            # primarily UDP, while some server setups expose TCP as well.
            for proto in ("udp", "tcp"):
                result = subprocess.run(
                    ["netstat", "-ano", "-p", proto],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=10,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    # UDP lines: UDP  0.0.0.0:33000  *:*  PID
                    # TCP lines: TCP  0.0.0.0:33000  ... PID
                    if len(parts) >= 4 and parts[0].upper() == proto.upper():
                        local = parts[1]
                        if local.rsplit(":", 1)[-1] == str(port):
                            return True
        except Exception as e:
            self.log(f"Could not check server listen port: {e}")
        return False

    def is_server_running(self):
        """Authoritative-ish running check: process first, listen port second."""
        return self.is_shipping_server_running() or self.is_server_port_open()

    def find_running_server_process(self):
        # The real Palworld server is the shipping executable. Do not rely on
        # self.server_process because PalServer.exe may have already exited.
        if self.is_server_running():
            return self.server_process or True

        if self.server_process is not None:
            try:
                if self.server_process.poll() is None:
                    return self.server_process
            except Exception:
                pass

        return None

    def toggle_server(self):
        # Do NOT decide from a momentary tasklist query. PalServer.exe can
        # spawn/restart the real Shipping process while the query is running.
        # The button state is the user's explicit intent: when it says Stop,
        # always execute the stop routine; when it says Start, start the server.
        try:
            button_text = self.start_button.cget("text")
        except Exception:
            button_text = "Start Server"

        if str(button_text).strip().lower().startswith("stop"):
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        if self.server_starting or self.server_stopping:
            self.log("Server operation is already in progress.")
            return

        if self.is_server_running():
            self.status_label.configure(text="Status: Running", text_color="#2b8a3e")
            self.start_button.configure(text="Stop Server")
            return

        server_path = self.config.get("server_path") or (
            self.server_path_entry.get().strip()
        )

        paths = detect_server_paths(server_path)

        if not paths["pal_server_path"]:
            messagebox.showerror(
                "Server not found",
                "PalServer.exe was not found. Detect the server first."
            )
            return

        self.apply_server_paths(paths)

        exe = paths["pal_server_path"]
        cwd = os.path.dirname(exe)
        self.server_exe_path = exe
        self.shipping_exe_path = self.get_shipping_server_path(paths["server_path"])

        if self.shipping_exe_path:
            self.log(f"Real server executable: {self.shipping_exe_path}")
        else:
            self.log("Warning: PalServer-Win64-Shipping.exe was not found yet; it will be detected after launch.")

        try:
            # Palworld does NOT use PublicPort from the INI to change the
            # listening socket. The actual listen port must be supplied as
            # the PalServer.exe startup argument -port=XXXX.
            ini_path = paths.get("ini_path", "")
            configured_port = self.config.get("server_port", "8211")
            listen_port = get_server_listen_port(ini_path, configured_port)

            command = [exe, f"-port={listen_port}"]

            self.log("Starting Palworld server...")
            self.log(f"Listen port from configuration: {listen_port}")
            self.log(
                "Startup command: " +
                " ".join(f'\"{x}\"' if " " in x else x for x in command)
            )

            self.server_starting = True
            self.server_start_time = __import__("time").monotonic()

            self.server_process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )

            # The launcher has started, but the dedicated server may still
            # be initializing. Keep the UI in Starting until process/port or
            # the server's own console output confirms readiness.
            self.status_label.configure(text="Status: Starting...", text_color="orange")
            self.start_button.configure(text="Stop Server")

            threading.Thread(
                target=self.read_server_output,
                daemon=True
            ).start()

        except Exception as e:
            self.log(f"Could not start server: {e}")
            self.server_process = None

    def read_server_output(self):
        process = self.server_process
        if not process or not process.stdout:
            return

        try:
            for line in iter(process.stdout.readline, ""):
                if line:
                    clean_line = line.rstrip()
                    self.log(f"[Server] {clean_line}")

                    # PalServer.exe itself prints this when the real server
                    # has finished initialization. Use it as an immediate
                    # confirmation instead of waiting for process polling.
                    if "Running Palworld dedicated server on" in clean_line:
                        self.server_starting = False
                        self.after(0, lambda: (
                            self.status_label.configure(text="Status: Running", text_color="#2b8a3e"),
                            self.start_button.configure(text="Stop Server")
                        ))
        except Exception as e:
            self.log(f"Server output reader stopped: {e}")

    def stop_server(self):
        """Stop Palworld cleanly without allowing PalServer.exe to respawn it.

        PalServer.exe is the launcher and PalServer-Win64-Shipping.exe is the
        real server. The launcher is stopped FIRST so it cannot immediately
        create another Shipping process after the child is killed. Then every
        Shipping process is force-terminated and verified.
        """
        self.server_starting = False
        self.server_stopping = True
        self.log("Stopping Palworld server...")
        self.status_label.configure(text="Status: Stopping...", text_color="orange")
        self.start_button.configure(text="Stopping...")

        # 1) Stop the launcher that can respawn the real server.
        try:
            if self.server_process is not None and self.server_process.poll() is None:
                pid = self.server_process.pid
                self.log(f"Stopping PalServer.exe launcher PID {pid}...")
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20
                )
        except Exception as e:
            self.log(f"Could not stop PalServer.exe launcher: {e}")

        # 2) Give the launcher a moment to exit before killing Shipping.
        threading.Event().wait(0.8)

        # 3) Kill the ACTUAL server process. Use both PID and image-name
        # termination because the launcher may already have detached.
        pids = self.get_shipping_process_pids()
        self.log("Target server executable: PalServer-Win64-Shipping.exe")
        if pids:
            self.log("Found server PID(s): " + ", ".join(map(str, pids)))
            for pid in pids:
                try:
                    result = subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20
                    )
                    output = ((result.stdout or "") + (result.stderr or "")).strip()
                    if output:
                        self.log(output)
                except Exception as e:
                    self.log(f"Failed to kill PID {pid}: {e}")
        else:
            self.log("No Shipping PID found by tasklist; trying image-name termination anyway.")

        try:
            result = subprocess.run(
                ["taskkill", "/IM", "PalServer-Win64-Shipping.exe", "/T", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20
            )
            output = ((result.stdout or "") + (result.stderr or "")).strip()
            if output:
                self.log(output)
        except Exception as e:
            self.log(f"Final Shipping image kill failed: {e}")

        self.server_process = None

        # 4) Verify for several seconds. If the launcher managed to respawn
        # Shipping, kill it again. This prevents the Stop button from turning
        # into an accidental Start operation.
        still_running = []
        for attempt in range(12):
            still_running = self.get_shipping_process_pids()
            if not still_running:
                break
            self.log(
                f"Shipping process still running (attempt {attempt + 1}/12): "
                + ", ".join(map(str, still_running))
            )
            for pid in still_running:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10
                    )
                except Exception as e:
                    self.log(f"Retry kill failed for PID {pid}: {e}")
            threading.Event().wait(0.5)

        if still_running:
            self.log(
                "ERROR: PalServer-Win64-Shipping.exe is STILL RUNNING: "
                + ", ".join(map(str, still_running))
            )
            self.server_stopping = False
            self.status_label.configure(text="Status: Stop failed", text_color="crimson")
            self.start_button.configure(text="Stop Server")
            return

        self.server_stopping = False
        self.status_label.configure(text="Status: Stopped", text_color="crimson")
        self.start_button.configure(text="Start Server")
        self.log("Palworld server stopped successfully.")

    def monitor_server(self):
        # Do not let the 1-second monitor race the server startup. PalServer.exe
        # is only a launcher and can exit before PalServer-Win64-Shipping.exe
        # has finished starting. During that window the old monitor incorrectly
        # changed the UI back to Stopped.
        try:
            if self.server_stopping:
                # stop_server() owns the final state while it is running.
                return

            running = self.is_server_running()

            if running:
                self.server_starting = False
                self.status_label.configure(
                    text="Status: Running",
                    text_color="#2b8a3e"
                )
                self.start_button.configure(text="Stop Server")

            elif self.server_starting:
                # Give the real Shipping executable time to appear. Depending on
                # the machine, Palworld can take several seconds to initialize.
                elapsed = __import__("time").monotonic() - self.server_start_time
                if elapsed < 60:
                    self.status_label.configure(
                        text=f"Status: Starting... ({int(elapsed)}s)",
                        text_color="orange"
                    )
                    self.start_button.configure(text="Stop Server")
                else:
                    self.server_starting = False
                    self.server_process = None
                    self.status_label.configure(
                        text="Status: Stopped",
                        text_color="crimson"
                    )
                    self.start_button.configure(text="Start Server")
                    self.log("Server did not start within 60 seconds.")

            else:
                self.server_process = None
                self.status_label.configure(
                    text="Status: Stopped",
                    text_color="crimson"
                )
                self.start_button.configure(text="Start Server")
        finally:
            if self.winfo_exists():
                self.after(1000, self.monitor_server)

    def update_server_thread(self):
        if self.find_running_server_process():
            messagebox.showwarning(
                "Server running",
                "Stop the Palworld server before updating it."
            )
            return

        self.run_thread(self.update_server)

    def update_server(self):
        steamcmd = self.steamcmd_entry.get().strip()
        if not steamcmd:
            steamcmd = self.config.get("steamcmd_path") or detect_steamcmd()

        server_dir = self.server_path_entry.get().strip()

        if not steamcmd or not os.path.isfile(steamcmd):
            self.log("SteamCMD was not found.")
            return

        if not server_dir:
            self.log("Server path is empty.")
            return

        self.log("Updating Palworld Dedicated Server...")

        success = install_palworld_server(
            steamcmd, server_dir, self.log
        )

        if success:
            self.log("Palworld server update completed.")
            self.config["steamcmd_path"] = normalize_path(steamcmd)
            save_config(self.config)

    # ========================================================
    # WORKSHOP MOD MANAGER
    # ========================================================

    def add_mod(self):
        text = self.mod_id_entry.get().strip()
        workshop_id = extract_workshop_id(text)

        if not workshop_id:
            messagebox.showerror(
                "Invalid Workshop ID",
                "Enter a Steam Workshop URL or numeric Workshop ID."
            )
            return

        mods = self.config.setdefault("mods", [])

        for mod in mods:
            if str(mod.get("id", "")) == workshop_id:
                messagebox.showinfo(
                    "Already added",
                    f"Workshop mod {workshop_id} is already in the list."
                )
                return

        mods.append({
            "id": workshop_id,
            "enabled": True,
            "name": "",
            "package": "",
        })

        save_config(self.config)
        self.mod_id_entry.delete(0, "end")
        self.log(f"Added Workshop mod: {workshop_id}")
        self.refresh_mod_list()

    def remove_mod(self, workshop_id):
        self.config["mods"] = normalize_mod_list(
            self.config.get("mods", [])
        )
        self.config["mods"] = [
            m for m in self.config["mods"]
            if str(m.get("id", "")) != str(workshop_id)
        ]
        save_config(self.config)
        self.refresh_mod_list()
        self.log(f"Removed Workshop mod: {workshop_id}")

    def toggle_mod(self, workshop_id, enabled):
        self.config["mods"] = normalize_mod_list(
            self.config.get("mods", [])
        )

        for mod in self.config["mods"]:
            if str(mod.get("id", "")) == str(workshop_id):
                mod["enabled"] = bool(enabled)

        save_config(self.config)

    def refresh_mod_list(self):
        if not hasattr(self, "mod_list"):
            return

        for widget in self.mod_list.winfo_children():
            widget.destroy()

        root = self.workshop_entry.get().strip()
        if not root:
            root = self.config.get("workshop_dir", "")

        mods = normalize_mod_list(self.config.get("mods", []))
        self.config["mods"] = mods

        if not mods:
            ctk.CTkLabel(
                self.mod_list,
                text="No Workshop mods added."
            ).grid(row=0, column=0, padx=10, pady=20)
            return

        for index, mod in enumerate(mods):
            workshop_id = str(mod.get("id", ""))
            mod_dir = find_workshop_mod(root, workshop_id)

            info = get_mod_info(mod_dir)
            display_name = get_mod_display_name(info)
            package = get_package_name(info)

            if not display_name:
                display_name = mod.get("name") or (
                    f"Workshop Mod {workshop_id}"
                )

            if package:
                mod["package"] = package
            if display_name:
                mod["name"] = display_name

            supports_server = mod_supports_server(info)

            var = ctk.BooleanVar(
                value=bool(mod.get("enabled", True))
            )

            check = ctk.CTkCheckBox(
                self.mod_list,
                text="",
                variable=var,
                command=lambda mid=workshop_id, v=var:
                    self.toggle_mod(mid, v.get())
            )
            check.grid(
                row=index, column=0,
                padx=8, pady=8, sticky="w"
            )

            text = f"{display_name}\nID: {workshop_id}"

            if package:
                text += f"\nPackage: {package}"

            if mod_dir:
                text += "\nDownloaded: Yes"
            else:
                text += "\nDownloaded: No"

            if not supports_server:
                text += "\nWARNING: Mod does not declare server support"

            label = ctk.CTkLabel(
                self.mod_list,
                text=text,
                justify="left",
                anchor="w"
            )
            label.grid(
                row=index, column=1,
                padx=8, pady=8,
                sticky="ew"
            )

            ctk.CTkButton(
                self.mod_list,
                text="Remove",
                width=90,
                command=lambda mid=workshop_id:
                    self.remove_mod(mid)
            ).grid(
                row=index, column=2,
                padx=8, pady=8
            )

        save_config(self.config)

    def get_workshop_root(self):
        root = self.workshop_root_entry.get().strip()

        if not root:
            root = self.config.get(
                "steamcmd_workshop_root",
                DEFAULT_WORKSHOP_ROOT
            )

        return normalize_path(root)

    def get_workshop_content(self):
        # First use an explicitly selected/known existing content directory.
        content = self.workshop_entry.get().strip()
        if content and os.path.isdir(content):
            content = normalize_path(content)
            # Accept an explicitly selected Workshop content directory, but
            # don't mistake the Workshop Root itself for the content folder.
            if os.path.basename(content).lower() == PALWORLD_WORKSHOP_APP_ID:
                return content
            for mod in self.config.get("mods", []):
                mid = str(mod.get("id", ""))
                if mid and os.path.isdir(os.path.join(content, mid)):
                    return content

        root = self.get_workshop_root()

        # Normal layout when SteamCMD honors the selected root:
        #   <root>\steamapps\workshop\content\1623730
        content = workshop_content_path_from_root(root)
        if content:
            return content

        # SteamCMD Workshop downloads can also be placed relative to the
        # SteamCMD installation itself. This is important because
        # +force_install_dir controls the game install directory, while
        # Workshop content may still end up under SteamCMD\steamapps.
        steamcmd = self.steamcmd_entry.get().strip()
        if not steamcmd:
            steamcmd = self.config.get("steamcmd_path", "")

        if steamcmd:
            steamcmd_dir = os.path.dirname(normalize_path(steamcmd))
            candidates = [
                os.path.join(
                    steamcmd_dir, "steamapps", "workshop",
                    "content", PALWORLD_WORKSHOP_APP_ID
                ),
                os.path.join(
                    steamcmd_dir, "steamapps", "workshop",
                    "content", PALWORLD_WORKSHOP_APP_ID,
                ),
            ]
            for candidate in candidates:
                if os.path.isdir(candidate):
                    return normalize_path(candidate)

        # Finally search the configured root and SteamCMD directory for the
        # actual 1623730 content directory. This also handles an existing
        # SteamCMD setup whose directory structure differs slightly.
        search_roots = [root]
        if steamcmd:
            search_roots.append(
                os.path.dirname(normalize_path(steamcmd))
            )

        for search_root in search_roots:
            if not search_root or not os.path.isdir(search_root):
                continue
            found = find_directory(
                search_root, PALWORLD_WORKSHOP_APP_ID, max_depth=8
            )
            if found and "workshop" in found.lower():
                return normalize_path(found)

        # Return the expected path even before SteamCMD has created it.
        return os.path.join(
            root, "steamapps", "workshop",
            "content", PALWORLD_WORKSHOP_APP_ID
        )

    def download_selected_mods(self):
        self.run_thread(self.download_mods)

    def download_mods(self):
        steamcmd = self.steamcmd_entry.get().strip()
        if not steamcmd:
            steamcmd = self.config.get("steamcmd_path") or detect_steamcmd()

        if not steamcmd or not os.path.isfile(steamcmd):
            self.log("SteamCMD was not found.")
            return

        root = self.get_workshop_root()
        ensure_workshop_root(root)

        mods = [
            m for m in self.config.get("mods", [])
            if m.get("enabled", True)
        ]

        if not mods:
            self.log("No enabled Workshop mods.")
            return

        self.config["steamcmd_path"] = normalize_path(steamcmd)
        self.config["steamcmd_workshop_root"] = root
        save_config(self.config)

        self.log(f"Workshop Root: {root}")

        # Important: force SteamCMD's workshop files into the selected
        # root instead of the user's normal Steam library.
        steam_username = self.steam_username_entry.get().strip()
        self.config["steam_username"] = steam_username

        arguments = [
            "+force_install_dir", root,
            "+login", steam_username if steam_username else "anonymous",
        ]

        for mod in mods:
            workshop_id = str(mod.get("id", ""))
            if not workshop_id:
                continue

            self.log(
                f"Downloading/updating Workshop mod {workshop_id}..."
            )

            arguments += [
                "+workshop_download_item",
                PALWORLD_WORKSHOP_APP_ID,
                workshop_id,
            ]

        arguments += ["+quit"]

        # Use a visible SteamCMD console when a Steam username is supplied.
        # This allows password + Steam Guard prompts to be completed normally.
        success = run_steamcmd(
            steamcmd,
            arguments,
            self.log,
            cwd=os.path.dirname(steamcmd),
            interactive=bool(steam_username),
        )

        if not success:
            self.log(
                "Workshop download failed. SteamCMD did not download the "
                "requested item. No successful download will be reported."
            )
            self.after(0, self.refresh_mod_list)
            save_config(self.config)
            return

        # SteamCMD can sometimes return exit code 0 even when an individual
        # Workshop item failed, so verify every requested item on disk.
        content = self.get_workshop_content()
        missing = []
        for mod in mods:
            mid = str(mod.get("id", ""))
            mod_dir = find_workshop_mod(content, mid)
            if not mod_dir or not os.listdir(mod_dir):
                missing.append(mid)

        if missing:
            self.log(
                "Workshop download finished, but these item(s) were not "
                "found on disk: " + ", ".join(missing)
            )
            self.log(
                "If SteamCMD showed 'Download item ... failed', use a "
                "Steam account that owns Palworld and complete Steam Guard "
                "in the SteamCMD window."
            )
            self.after(0, self.refresh_mod_list)
            save_config(self.config)
            return

        self.log("Workshop download/update completed successfully.")

        content = self.get_workshop_content()
        if os.path.isdir(content):
            self.config["workshop_dir"] = content
            self.after(
                0,
                lambda c=content: self.set_entry(
                    self.workshop_entry, c
                )
            )
            self.log(f"Workshop content detected: {content}")

        save_config(self.config)
        self.after(0, self.refresh_mod_list)

    def sync_mods(self):
        self.run_thread(self.sync_mods_worker)

    def sync_mods_worker(self):
        source = self.get_workshop_content()

        if not os.path.isdir(source):
            self.log(
                f"Workshop content directory does not exist: {source}"
            )
            return

        destination = self.server_workshop_entry.get().strip()

        if not destination:
            server_dir = self.config.get("server_path", "")
            destination = os.path.join(
                server_dir, "Mods", "Workshop"
            )

        destination = normalize_path(destination)
        os.makedirs(destination, exist_ok=True)

        selected = [
            m for m in self.config.get("mods", [])
            if m.get("enabled", True)
        ]

        if not selected:
            self.log("No enabled mods to copy.")
            return

        copied = 0

        for mod in selected:
            workshop_id = str(mod.get("id", ""))
            src = find_workshop_mod(source, workshop_id)

            if not src:
                self.log(
                    f"Workshop mod {workshop_id} is not downloaded."
                )
                continue

            dst = os.path.join(destination, workshop_id)

            try:
                safe_copytree(src, dst)
                copied += 1
                self.log(
                    f"Copied {workshop_id} -> {dst}"
                )
            except Exception as e:
                self.log(
                    f"Failed to copy {workshop_id}: {e}"
                )

        self.config["server_workshop_dir"] = destination
        save_config(self.config)

        self.after(
            0,
            lambda: self.set_entry(
                self.server_workshop_entry, destination
            )
        )

        self.log(f"Copied {copied} Workshop mod(s) to server.")

    # ========================================================
    # APPLY MOD SETTINGS
    # ========================================================

    def apply_mod_settings(self):
        self.run_thread(self.apply_mod_settings_worker)

    def apply_mod_settings_worker(self):
        server_dir = self.config.get("server_path", "")
        if not server_dir:
            server_dir = self.server_path_entry.get().strip()

        paths = detect_server_paths(server_dir)

        if not paths["server_path"]:
            self.log("Server path is not detected.")
            return

        self.apply_server_paths(paths)

        workshop_content = self.get_workshop_content()

        selected_packages = []

        for mod in self.config.get("mods", []):
            if not mod.get("enabled", True):
                continue

            workshop_id = str(mod.get("id", ""))
            mod_dir = find_workshop_mod(
                workshop_content, workshop_id
            )

            if not mod_dir:
                self.log(
                    f"Skipping {workshop_id}: not downloaded."
                )
                continue

            info = get_mod_info(mod_dir)
            package = get_package_name(info)

            if not package:
                package = mod.get("package", "")

            if not package:
                self.log(
                    f"Skipping {workshop_id}: PackageName not found in Info.json."
                )
                continue

            if not mod_supports_server(info):
                self.log(
                    f"Warning: {package} does not declare server support."
                )

            if package not in selected_packages:
                selected_packages.append(package)

        # WorkshopRootDir should point to the actual directory containing
        # Workshop item IDs, not the SteamCMD installation directory.
        root_for_palmod = normalize_path(workshop_content)

        path = paths["pal_mod_settings_path"]

        success = write_pal_mod_settings(
            path,
            root_for_palmod,
            selected_packages
        )

        if success:
            self.config["pal_mod_settings_path"] = path
            self.config["workshop_dir"] = root_for_palmod
            save_config(self.config)

            self.log(f"PalModSettings.ini updated: {path}")
            self.log(f"WorkshopRootDir={root_for_palmod}")

            if selected_packages:
                self.log(
                    "Active mods: " +
                    ", ".join(selected_packages)
                )
            else:
                self.log("No active Workshop mods.")

            self.log(
                "A .backup file was created when an existing "
                "PalModSettings.ini was present."
            )
        else:
            self.log("Failed to write PalModSettings.ini.")

    # ========================================================
    # SERVER SETTINGS
    # ========================================================

    def load_ini_settings(self):
        for widget in self.settings_scroll.winfo_children():
            widget.destroy()

        self.setting_entries = {}

        path = self.config.get("ini_path", "")

        if not path or not os.path.isfile(path):
            self.ini_path_label.configure(
                text="INI: Not detected"
            )
            ctk.CTkLabel(
                self.settings_scroll,
                text=(
                    "PalWorldSettings.ini was not found.\n"
                    "Detect the server first."
                ),
                justify="left"
            ).grid(row=0, column=0, padx=10, pady=10)
            return

        self.ini_path_label.configure(
            text=f"INI: {path}"
        )

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()

            settings = parse_option_settings(content)

            if not settings:
                ctk.CTkLabel(
                    self.settings_scroll,
                    text="No OptionSettings values were found."
                ).grid(row=0, column=0, padx=10, pady=10)
                return

            for row, (key, value) in enumerate(settings.items()):
                ctk.CTkLabel(
                    self.settings_scroll,
                    text=key,
                    anchor="w"
                ).grid(
                    row=row, column=0,
                    sticky="w", padx=8, pady=5
                )

                entry = ctk.CTkEntry(
                    self.settings_scroll
                )
                entry.grid(
                    row=row, column=1,
                    sticky="ew", padx=8, pady=5
                )
                entry.insert(0, value)

                self.setting_entries[key] = entry

        except Exception as e:
            self.log(f"Could not load INI: {e}")

    def save_ini_settings(self):
        path = self.config.get("ini_path", "")

        if not path or not os.path.isfile(path):
            messagebox.showerror(
                "INI not found",
                "PalWorldSettings.ini was not found."
            )
            return

        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()

            backup = path + ".backup"

            try:
                shutil.copy2(path, backup)
            except Exception:
                pass

            changed = 0

            for key, entry in self.setting_entries.items():
                value = entry.get()
                content, did_change = update_option_setting(
                    content, key, value
                )
                if did_change:
                    changed += 1

            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

            self.log(
                f"Server settings saved. Changed values: {changed}"
            )

        except Exception as e:
            messagebox.showerror(
                "Save failed",
                str(e)
            )

    # ========================================================
    # CLOSE
    # ========================================================

    def on_close(self):
        if self.find_running_server_process():
            answer = messagebox.askyesno(
                "Server is running",
                "Palworld server is still running.\n\n"
                "Stop it and close the manager?"
            )

            if not answer:
                return

            self.stop_server()

        self.config["steamcmd_path"] = (
            self.steamcmd_entry.get().strip()
        )

        if hasattr(self, "steam_username_entry"):
            self.config["steam_username"] = (
                self.steam_username_entry.get().strip()
            )

        self.config["server_path"] = (
            self.server_path_entry.get().strip()
        )

        self.config["steamcmd_workshop_root"] = (
            self.workshop_root_entry.get().strip()
        )

        self.config["workshop_dir"] = (
            self.workshop_entry.get().strip()
        )

        self.config["server_workshop_dir"] = (
            self.server_workshop_entry.get().strip()
        )

        save_config(self.config)
        self.destroy()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    app = PalworldManagerApp()
    app.mainloop()
