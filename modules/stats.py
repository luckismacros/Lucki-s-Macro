# modules/stats.py
"""
Per-session counters: what actually happened during a run.

A farming bot's whole purpose is throughput over hours, and until now the only
record of a run was a scrolling log you had to read backwards to answer "did that
overnight session actually accomplish anything?". These are the numbers worth
knowing at a glance - how many matches, how they went, and how much of the time was
spent recovering rather than farming.

A module-level SESSION instance exists so code that is nowhere near the GUI can
record an event without having a reference plumbed through to it - reconnect.py
counting a recovered disconnect being the case that would otherwise have needed
threading a stats object through nine call sites in three modules.
"""
import time


def format_duration(seconds):
    """Compact human duration: 45s, 12m, 3h04m."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


class SessionStats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.started_at = None
        self.matches = 0
        self.victories = 0
        self.defeats = 0
        self.disconnects = 0
        self.rewards_picked = 0
        self.challenges_skipped = 0
        self.restarts = 0
        self._match_started_at = None
        self._match_durations = []

    def start(self):
        self.reset()
        self.started_at = time.time()

    @property
    def elapsed(self):
        return 0.0 if self.started_at is None else time.time() - self.started_at

    @property
    def avg_match(self):
        if not self._match_durations:
            return 0.0
        return sum(self._match_durations) / len(self._match_durations)

    def match_started(self):
        self.matches += 1
        self._match_started_at = time.time()

    def _end_match(self):
        if self._match_started_at is not None:
            self._match_durations.append(time.time() - self._match_started_at)
            self._match_started_at = None

    def victory(self):
        self.victories += 1
        self._end_match()

    def defeat(self):
        self.defeats += 1
        self._end_match()

    def disconnect(self):
        self.disconnects += 1
        # Deliberately does NOT end the match: a disconnect means this match never
        # finished, so folding its partial time into the average would drag it toward
        # meaninglessness. The next match_started() overwrites the pending timestamp.
        self._match_started_at = None

    def reward_picked(self):
        self.rewards_picked += 1

    def challenge_skipped(self):
        self.challenges_skipped += 1

    def restarted(self):
        """A Never Stop restart after the run stopped on its own."""
        self.restarts += 1

    def line(self):
        """One compact line for the status bar."""
        if self.started_at is None:
            return "No run yet"

        parts = [f"{format_duration(self.elapsed)} elapsed", f"{self.matches} matches"]
        if self.victories or self.defeats:
            parts.append(f"{self.victories}W / {self.defeats}L")
        if self.rewards_picked:
            parts.append(f"{self.rewards_picked} rewards")
        if self.disconnects:
            parts.append(f"{self.disconnects} reconnects")
        if self.restarts:
            parts.append(f"{self.restarts} restarts")
        if self.avg_match:
            parts.append(f"avg {format_duration(self.avg_match)}/match")
        return "   ".join(parts)

    def notify_fields(self):
        """
        The same numbers as report(), as Discord embed fields (see modules.notify.send's
        `fields` param) instead of a text block - structured side-by-side numbers read
        better in an embed than a wall of text does, and this keeps that formatting
        decision in one place rather than duplicated into gui.py.

        Returns [] when nothing happened yet, same guard as report().
        """
        if self.started_at is None or self.matches == 0:
            return []

        fields = [
            ("Elapsed", format_duration(self.elapsed)),
            ("Matches", str(self.matches)),
        ]
        if self.victories or self.defeats:
            fields.append(("Won / Lost", f"{self.victories} / {self.defeats}"))
            if self.victories + self.defeats:
                rate = 100.0 * self.victories / (self.victories + self.defeats)
                fields.append(("Win rate", f"{rate:.0f}%"))
        if self.avg_match:
            fields.append(("Avg match", format_duration(self.avg_match)))
        if self.rewards_picked:
            fields.append(("Portal rewards", str(self.rewards_picked)))
        if self.challenges_skipped:
            fields.append(("Skipped (cooldown)", str(self.challenges_skipped)))
        if self.disconnects:
            fields.append(("Reconnects", str(self.disconnects)))
        if self.restarts:
            fields.append(("Auto-restarts", str(self.restarts)))
        return fields

    def report(self):
        """Multi-line summary, logged when a run ends."""
        if self.started_at is None or self.matches == 0:
            return None

        lines = [
            f"Session summary - ran for {format_duration(self.elapsed)}:",
            f"  Matches played:      {self.matches}",
            f"  Won / Lost:          {self.victories} / {self.defeats}",
        ]
        if self.matches and (self.victories + self.defeats):
            rate = 100.0 * self.victories / (self.victories + self.defeats)
            lines.append(f"  Win rate:            {rate:.0f}%")
        if self.avg_match:
            lines.append(f"  Average match:       {format_duration(self.avg_match)}")
        if self.rewards_picked:
            lines.append(f"  Portal rewards:      {self.rewards_picked}")
        if self.challenges_skipped:
            lines.append(f"  Challenges skipped:  {self.challenges_skipped} (on cooldown)")
        if self.disconnects:
            lines.append(f"  Disconnects handled: {self.disconnects}")
        if self.restarts:
            lines.append(f"  Auto-restarts:       {self.restarts}")
        return "\n".join(lines)


# Shared instance. See the module docstring for why this is global.
SESSION = SessionStats()
