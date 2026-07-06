# Droid Alerts Technical Notes

Everything here is for people who want to run Droid Alerts from the command
line, understand how the detection works, or contribute captures. If you just
want alerts, the [README](README.md) covers it.

## Running from the command line

Install dependencies once with `pip install -r requirements.txt`, then:

```
python main.py gui                    # open the app (same as the .bat file)
python main.py watch                  # run the watcher in the terminal, no GUI
python main.py watch --extra-checks   # enable HDR / washed-out color fallback checks
python main.py watch --debug          # verbose output; numpad + saves a chat-box snapshot
python main.py calibrate              # drag-select the alert region manually
python main.py calibrate --reset      # go back to automatic region detection
python main.py build-templates        # rebuild templates/ from training_data/current_ui/
python main.py test                   # run the fixture evaluation harness
python main.py test --dump-unlabeled  # also dump review crops for unlabeled fixtures
```

Runtime templates are committed under `templates/`, so a fresh checkout can run
live detection right after installing dependencies.

The tool writes runtime data (logs, alert samples, debug screenshots) only
under its own folder. It never writes to OS user-data directories. The public
repo intentionally excludes local captures, logs, screenshots, and training
data.

## Building the Windows exe

For a release build, double-click `Build EXE.bat`.

It installs the build tools, runs PyInstaller, and creates:

```text
dist\Droid Alerts\Droid Alerts.exe
dist\DroidAlerts-Windows.zip
```

Share the zip with normal users. They only need to unzip it and double-click
`Droid Alerts.exe`; Python is not required on their PC.

## How detection works

1. **Region** (`src/droid_alerts/region.py`): the chat-alert area is found as a
   percent-of-screen band with left 0%, width 33%, and height 16%. Standard
   wide screens use top 47%. Ultrawide screens (aspect >= 2.20, e.g.
   3440x1392 and 3440x1440) use top 40%. Compact screens (aspect <= 1.50,
   e.g. 1440x1040) use top 36% because the alert rows sit higher. Manual calibration is stored as
   *percent ratios* in `config/calibration.json`, so it survives resolution
   changes.
2. **Scale normalization** (`normalize.py`): the captured band is resized so
   alert rows are 44px tall, which is the reference scale the templates and
   column constants were measured at. The game fits its HUD to a 16:9 box, so the
   scale is `min(width / 2560, height / 1440)`.
3. **Row seeding** (`row_finder.py`): resolution-relative foreground masks
   plus a spawn-phrase white-text profile locate candidate alert rows.
4. **Classification** (`classifier.py`): droid family is read from the droid
   name text (dark-outlined, glyph-sized colored components), with the icon
   window as fallback; rarity comes from word-shape templates plus
   text-colored component analysis that can override sand-inflated "Common"
   reads. Mythic alerts additionally require a rarity-specific word-shape
   match, which is what keeps background false positives out.
5. **Alerts** (`alerts.py`): per-combo score gates, cooldown, and row-hash
   dedupe; sound comes from `assets/sounds/*.wav` (first file found) or a
   system beep.

While watching, each unique non-alert spawn prints once as `[DETECTED]`.
Priority detections that pass the alert policy print as `[ALERT]` and fire the
enabled channels (sound, popup, ntfy, Pushover, Discord). Debug mode also
prints non-alert detections as `[SEEN]`.

Debug mode never saves screenshots automatically. Press numpad `+` during
`watch --debug` to save the current chat-box region plus a candidate-check
overlay.

## Testing

`python main.py test` runs every fixture in `tests/fixtures/` and writes a
scored report to `tests/results/`. Current private baseline: **51/51 labeled
fixtures pass with zero false positives and zero false negatives**, including
synthetic 1920x1080 / 2560x1440 / 3440x1440 stress renders and real 4:3
(1440x1040) captures.

`python tests/test_paths_guard.py` asserts the tool never references OS
user-data paths.

## Adding new training captures

1. Copy the screenshot into `training_data/current_ui/`.
2. Add crop entries in `tools/build_templates.py` (row top y, at reference
   scale; use `python main.py test --dump-unlabeled` review dumps to measure).
3. `python main.py build-templates`, then `python main.py test`.
