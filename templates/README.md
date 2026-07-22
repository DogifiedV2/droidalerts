# Templates

Runtime template PNGs used by the detector are committed here so live detection
works from a fresh checkout.

To refresh them in a private working copy, add captures under
`training_data/current_ui/`, update `tools/build_templates.py` if needed, then
run `python main.py build-templates`.

Galactic Epic/Legendary/Mythic rarity-ROI prototypes are built separately from
the reviewed debug corpus. They are preserved by the normal template rebuild:

```bash
PYTHONPATH=src python3 tools/build_galactic_roi_templates.py \
  --data-root '/Users/rubenvancraenenbroeck/Downloads/data 2'
```

The generator normalizes every reviewed real priority row, clusters similar
ROIs, and averages each cluster so changing scenery is suppressed. Provenance
is recorded in `galactic_rarity_rois_manifest.json`.
