"""Weekly reminder notification - meant to run right after the weekly
pipeline (see the launchd wrapper script), not on its own fixed schedule,
so it only fires once that week's run has actually finished, however long
it took.

Checks how many flagged stocks still need catalyst research and sends a
native macOS notification with the count. Deliberately does NOT do the
research itself: that needs real judgment (verifying publish dates, source
credibility) which should stay a human-approved, in-session action - see
catalysts.py's own docstring on why this layer was never automated
end-to-end.

Usage:
    python -m momentum.weekly_reminder
"""

from __future__ import annotations

import json
import subprocess

from momentum import holdings as hold
from momentum.pipeline import RUNS_DIR


def count_pending_catalyst_research() -> tuple[str | None, int, int]:
    latest_file = RUNS_DIR / "latest.txt"
    if not latest_file.exists():
        return None, 0, 0
    run_date = latest_file.read_text().strip()

    public_count = 0
    public_queries = RUNS_DIR / run_date / "catalyst_queries.json"
    if public_queries.exists():
        public_count = len(json.loads(public_queries.read_text()))

    holdings_count = 0
    holdings_queries = hold.HOLDINGS_RUNS_DIR / run_date / "catalyst_queries.json"
    if holdings_queries.exists():
        holdings_count = len(json.loads(holdings_queries.read_text()))

    return run_date, public_count, holdings_count


def send_notification(title: str, message: str) -> None:
    # osascript escaping: this project only ever feeds it known-safe
    # strings (a date, two counts), never anything else - no quotes to
    # worry about, but double-escape defensively if that ever changes.
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def run_weekly_reminder() -> None:
    run_date, public_count, holdings_count = count_pending_catalyst_research()
    if run_date is None:
        send_notification("Momentum Dashboard", "No pipeline run found yet.")
        return

    total = public_count + holdings_count
    if total == 0:
        send_notification(
            "Momentum Dashboard",
            f"{run_date} run: catalyst research is up to date - nothing pending.",
        )
        return

    parts = []
    if public_count:
        parts.append(f"{public_count} public")
    if holdings_count:
        parts.append(f"{holdings_count} holdings")
    breakdown = " + ".join(parts)
    send_notification(
        "Momentum Dashboard - catalyst research pending",
        f"{run_date} run: {total} stocks need catalyst research ({breakdown}). "
        "Ask Claude Code to research them.",
    )
    print(f"[weekly-reminder] {run_date}: {total} stocks pending ({breakdown}) - notification sent")


if __name__ == "__main__":
    run_weekly_reminder()
