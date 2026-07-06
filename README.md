# ToolV2 — Cross-PC Droid Tycoon Alert Detector

Watches the game's droid-spawn chat alerts and plays a sound on the five
priority combos: **Diamond Mythic, Rainbow Mythic, Beskar Mythic,
Beskar Legendary, Beskar Epic**.

Works on any PC/monitor/resolution: the capture region is found as a
percent-of-screen band (no manual pixel dragging needed), and every frame is
scale-normalized to a 44px-row reference before the classifier runs, so the
proven fixed-column detection logic stays valid everywhere.

The tool writes runtime data only under this project folder. The public repo
includes the generated runtime templates, but intentionally excludes local
captures, logs, generated screenshots, and training data.

## Usage

```
python main.py watch            # run the live watcher
python main.py watch --debug    # verbose + numpad + saves the current chat box/candidate check
python main.py calibrate        # optional: drag the alert region manually
python main.py calibrate --reset  # back to automatic region detection
python main.py build-templates  # rebuild templates/ from training_data/current_ui/
python main.py test             # run the fixture evaluation harness
python main.py test --dump-unlabeled  # also dump review crops for unlabeled fixtures
```

Install deps: `pip install -r requirements.txt`

Runtime templates are committed under `templates/`, so a fresh checkout can run
live detection after installing dependencies.

## How it works

1. **Region** (`src/toolv2/region.py`): auto-box at left 0%, top 47%,
   width 33%, height 16% of the screen (from the measured position notes).
   Manual calibration is stored as *percent ratios* in
   `config/calibration.json`, so it survives resolution changes.
2. **Scale normalization** (`normalize.py`): the band is resized so alert
   rows are 44px tall (scale = screen_height / 1440), the reference scale the
   templates and column constants were captured at.
3. **Row seeding** (`row_finder.py`): Tool V1's resolution-relative masks
   plus a precise spawn-phrase white-text profile locate candidate rows.
4. **Classification** (`classifier.py`): the reference detector, ported with
   targeted robustness fixes (icon window anchored to content-left,
   text-shaped color analysis allowed to override sand-inflated "Common",
   blur-tolerant thresholds — all measured against fixtures).
5. **Alerts** (`alerts.py`): per-combo score gates + cooldown + row-hash
   dedupe; sound via `assets/sounds/*.wav` (first file found) or a beep.

Live watch prints each unique non-alert spawn once as `[DETECTED]`. Priority
detections that pass the alert policy print as `[ALERT]` and play the alert
sound. Debug mode additionally prints non-alert detections as `[SEEN]`.

Debug mode does not save automatic screenshots. Press numpad `+` while
`watch --debug` is running to save the current chat-box region and a
candidate-check overlay for template review.

## Testing

In a private checkout with templates and fixtures populated, `python main.py
test` runs every fixture in `tests/fixtures/` and writes a scored report to
`tests/results/`. Current private baseline: **32/32 labeled fixtures pass with
zero false positives and zero false negatives**, including synthetic
1920x1080 / 2560x1440 / 3440x1440 stress renders with identical results.

`python tests/test_paths_guard.py` asserts the tool never references OS
user-data paths.

### Known gap

No real (non-template-source) **Beskar Mythic** full-row capture exists yet.
When one appears, drop it in `training_data/current_ui/` and
`tests/fixtures/real_captures/`, add its crops to `tools/build_templates.py`,
label it in `tests/fixtures/manifest.json`, rebuild and re-run the eval.

## Adding new training captures

1. Copy the screenshot into `training_data/current_ui/`.
2. Add crop entries in `tools/build_templates.py` (row top y, at reference
   scale; use `tests/run_eval.py --dump-unlabeled` review dumps to measure).
3. `python main.py build-templates`, then `python main.py test`.
