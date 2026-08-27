"""Preview or apply the Lakebase terminal-session retention policy."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from a3docklab.application.state import ApplicationStateStore, PostgresConnectionFactory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    if arguments.retention_days < 1:
        parser.error("--retention-days must be positive")
    cutoff = datetime.now(UTC) - timedelta(days=arguments.retention_days)
    store = ApplicationStateStore(PostgresConnectionFactory())
    candidates = store.count_expired_terminal_sessions(cutoff)
    removed = store.cleanup_terminal_sessions(cutoff) if arguments.apply else 0
    print(
        {
            "cutoff_utc": cutoff.isoformat(),
            "candidate_sessions": candidates,
            "removed_sessions": removed,
            "mode": "apply" if arguments.apply else "preview",
        }
    )


if __name__ == "__main__":
    main()
