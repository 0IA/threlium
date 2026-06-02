# E2E toolkit

Granular harness modules for e2e tests. Import from `tests.e2e.toolkit` (or a submodule, e.g. `tests.e2e.toolkit.mailflow`).

**Deploy note:** attach-only pytest runs do not refresh Python on the SUT container.
After product changes under `ansible/roles/threlium/files/scripts/threlium/`, run
`tests/e2e/wipe_bake.py` or `wipe_sync.py` (or `docker cp` for a quick local cycle).
