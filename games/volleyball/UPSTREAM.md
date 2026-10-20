Source: https://github.com/XHackerFinnX/volleyball-online
Commit: 8eb56c8513b57ff9db230ba0c1041f091ae5cd73

Browser assets are vendored here so File-Transfer can deploy the game without
another repository, Node.js, npm or a separate service. The browser configuration
and WebSocket URL resolution use `/volleyball/ws` on the same origin.

The authoritative server physics and room protocol were ported to Python in
`services/volleyball_physics.py` and `services/volleyball.py`. JavaScript physics
remains in the browser for player prediction, with acknowledged input replay and
smooth reconciliation in `docs/shared/prediction.js`. Mobile controls and
viewport/orientation handling are adapted for the FastAPI version.
Snapshot interpolation uses server timestamps, adaptive jitter buffering and
bounded extrapolation in `docs/shared/snapshots.js`. The Python server coalesces
outdated snapshots and measures both players' round-trip times for the match HUD.
Room metadata also carries the host's arena view; guests fit that view with
letterboxing instead of choosing an independent horizontal stretch.
Personal backgrounds use the supplied PNG catalog and browser storage; custom
player colors accept validated six-digit HEX values alongside preset indexes.
Continuous capsule collisions in Python and `docs/shared/net.js` also guard
player separation, local play and snapshot rendering against crossing the net.
See the root `VOLLEYBALL_RU.md`.
