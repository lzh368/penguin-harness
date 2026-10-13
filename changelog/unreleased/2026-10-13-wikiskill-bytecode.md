# Keep WikiSkill campaign runs from creating binary plugin files

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `skills`

[中文版](2026-10-13-wikiskill-bytecode.zh.md)

Running `campaign.py` with ordinary Python could create `.pyc` files beside its imported helpers. The built-in plugin loader rejects binary skill resources, so subsequent Agent operations could fail while loading the library. Campaign execution now disables bytecode writes before importing those helpers.

Existing generated `.pyc` files still need to be removed from the affected skill's `__pycache__` directory. Keep the Python sources and experiment data. The workspace launcher also suppresses bytecode for its in-process imports, helper commands, and service subprocesses.
