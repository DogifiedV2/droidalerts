# Templates

Runtime template PNGs used by the detector are committed here so live detection
works from a fresh checkout.

To refresh them in a private working copy, add captures under
`training_data/current_ui/`, update `tools/build_templates.py` if needed, then
run `python main.py build-templates`.
