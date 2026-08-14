"""Databricks App entry point for A3 DockLab mission replay."""

from __future__ import annotations

import os
from pathlib import Path

from a3docklab.visualization.dashboard import create_app
from a3docklab.visualization.replay import LocalReplayStore

app = create_app(LocalReplayStore(Path(os.getenv("A3DOCKLAB_BUNDLE_ROOT", "bundles"))))
server = app.server


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", "8000")), debug=False)

