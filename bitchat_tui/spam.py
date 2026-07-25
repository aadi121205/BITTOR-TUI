"""Per-sender duplicate/flood spam suppression, with a global on/off toggle."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SpamFilter:
    def __init__(
        self,
        duplicate_window: float = 30.0,
        duplicate_threshold: int = 3,
        flood_window: float = 10.0,
        flood_threshold: int = 5,
    ):
        self.enabled = True
        self.duplicate_window = duplicate_window
        self.duplicate_threshold = duplicate_threshold
        self.flood_window = flood_window
        self.flood_threshold = flood_threshold
        self._history: dict[str, deque[tuple[float, str]]] = defaultdict(lambda: deque(maxlen=20))

    def allow(self, pubkey_hex: str, content: str) -> bool:
        """Records the message and returns False if it should be suppressed as spam."""
        if not self.enabled:
            return True

        now = time.time()
        history = self._history[pubkey_hex]
        history.append((now, content))

        window = max(self.duplicate_window, self.flood_window)
        while history and now - history[0][0] > window:
            history.popleft()

        flood_count = sum(1 for ts, _ in history if now - ts <= self.flood_window)
        if flood_count > self.flood_threshold:
            return False

        normalized = content.strip().lower()
        duplicate_count = sum(
            1
            for ts, c in history
            if now - ts <= self.duplicate_window and c.strip().lower() == normalized
        )
        if duplicate_count > self.duplicate_threshold:
            return False

        return True
