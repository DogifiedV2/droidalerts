# Droid Alerts

Droid Alerts watches **Droid Tycoon** while you play and alerts you about
selected droid spawns, belt blueprints, limited deals, and rebirth events.

Alerts can be sent through:

- On-screen popups
- Sound
- Discord
- ntfy
- Pushover

The app supports monitor capture, Fortnite window capture, and USB capture
devices.

## Install

### Windows release

1. Download `DroidAlerts.zip` from the
   [latest GitHub release](https://github.com/DogifiedV2/droidalerts/releases/latest).
2. Extract the ZIP.
3. Open the extracted folder.
4. Run `Droid Alerts.exe`.

Linux and macOS users should run the app from source.

## Run from source

Install [Python 3.10 or newer](https://www.python.org/downloads/) and Git, then
use the instructions for your operating system.

### Windows

```powershell
git clone https://github.com/DogifiedV2/droidalerts.git
cd droidalerts
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py gui
```

You can also double-click `Start Droid Alerts.bat` after downloading the source.

### Linux

```bash
git clone https://github.com/DogifiedV2/droidalerts.git
cd droidalerts
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python main.py gui
```

Or use the launcher:

```bash
chmod +x "Start Droid Alerts.sh"
./Start\ Droid\ Alerts.sh
```

### macOS

```bash
git clone https://github.com/DogifiedV2/droidalerts.git
cd droidalerts
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python main.py gui
```

Allow screen recording when macOS asks for permission.

Or use the launcher:

```bash
chmod +x "Start Droid Alerts.command"
open "Start Droid Alerts.command"
```

## Basic usage

1. Select the monitor, Fortnite window, or capture device on the Dashboard.
2. Choose the alerts you want.
3. Enable the alert channels you want to use.
4. Click **Start Watching**.

For belt alerts:

1. Open **Belt Tracker**.
2. Select the belt region.
3. Choose the droids and minimum rarities.
4. Click **Start Tracking**.

## Extra information

- **Windows belt screenshot:** Enable **Blueprint collection mode**, start Belt
  Tracker, and press `P`. The selected belt region is saved under
  `data/belt_dev/session_.../manual_captures/`.
- Every manual belt screenshot has a matching JSON file showing what the
  detector accepted, rejected, or failed to detect, including names and
  rarities.
- Automatic unknown-candidate crops are not saved.
- **Windows chat debug screenshot:** Run the watcher in debug mode and press
  numpad `+`.
- Fortnite should remain restored. Windows may pause window capture while the
  game is minimized.
- Settings, history, screenshots, and logs are stored inside the project or
  extracted application folder.
- Linux capture cards use V4L2 or GStreamer. macOS capture cards use
  AVFoundation.
- Window selection is supported on Linux X11 and macOS. Wayland window
  selection is not supported yet. Capture cards remain available, while
  Wayland monitor capture must be validated for the desktop session.

Additional command-line options are documented in [EXTRA.md](EXTRA.md).
