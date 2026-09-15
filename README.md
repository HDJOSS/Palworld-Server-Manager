# Palworld Server Manager

A Windows GUI application for managing a Palworld Dedicated Server.

## Features

- Start Palworld Dedicated Server
- Stop Palworld Dedicated Server
- Detect already installed server
- SteamCMD server updates
- Steam Workshop mod management
- Download Workshop mods
- Update Workshop mods
- Apply Palworld mod settings
- Server port detection
- SteamCMD authentication
- Persistent configuration

## Requirements

- Windows 10/11
- Python 3.x
- SteamCMD
- Palworld Dedicated Server

## Note

Server manager generates config file in to the "AppData\Roaming\PalworldServerConfig\server_config.json" (Server Manager needs this file so that it doesn't forget the settings you configure within it.)

## Installation


Install the required Python packages: (Not required, if you download .exe fail)

```bash
pip install -r requirements.txt
