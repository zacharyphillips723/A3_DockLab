"""Portable run storage contract and local filesystem implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

from a3docklab.run_metadata import RunMetadata


class RunStorage(Protocol):
    """Boundary implemented locally now and by platform adapters later."""

    def write_run(
        self, telemetry: pd.DataFrame, metadata: RunMetadata, events: pd.DataFrame | None = None
    ) -> None: ...


class LocalRunStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write_run(
        self, telemetry: pd.DataFrame, metadata: RunMetadata, events: pd.DataFrame | None = None
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        telemetry.to_csv(self.root / "telemetry.csv", index=False)
        (self.root / "metadata.json").write_text(
            metadata.model_dump_json(indent=2), encoding="utf-8"
        )
        if events is not None:
            events.to_csv(self.root / "events.csv", index=False)
