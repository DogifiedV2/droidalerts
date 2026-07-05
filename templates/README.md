# Templates

Runtime template PNGs are generated from local training captures and are not
committed to the public source repo.

To populate this folder in a private working copy, add captures under
`training_data/current_ui/`, update `tools/build_templates.py` if needed, then
run:

```powershell
python main.py build-templates
```
