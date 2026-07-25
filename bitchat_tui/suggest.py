"""Inline autocomplete for the chat input: slash commands, alias/nickname
arguments to those commands, and @mentions anywhere in a message.

Built on Textual's Suggester: get_suggestion(value) must return a string that
starts with the exact `value` the user typed (Input splices in only the tail,
and replaces the whole line with our return value on accept) -- so every
branch here is careful to return `value + <new suffix>`, never a rebuilt
string, to avoid silently changing casing the user already typed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from textual.suggester import Suggester

from . import contacts

if TYPE_CHECKING:
    from .app import BitchatTUIApp

COMMANDS = [
    "/join", "/nick", "/whoami", "/contacts", "/addcontact", "/dm", "/npub",
    "/block", "/unblock", "/blocked", "/quit", "/exit",
]

# Commands whose single first argument is an alias/nickname worth completing.
ALIAS_ARG_COMMANDS = {"/dm", "/block", "/unblock", "/npub"}

_MENTION_TAIL_RE = re.compile(r"@(\w*)$")


class ChatSuggester(Suggester):
    def __init__(self, app: "BitchatTUIApp"):
        # Caching disabled: candidates (contacts, live channel participants) change
        # while chatting, so the same typed prefix can validly match different things
        # a minute later -- a cached miss would otherwise get stuck.
        super().__init__(case_sensitive=True, use_cache=False)
        self._app = app

    def _alias_candidates(self) -> list[str]:
        names = set(contacts.load().keys())
        if self._app.session is not None:
            names.update(p.nickname for p in self._app.session.participants.values() if p.nickname)
        return sorted(names)

    def _channel_nicknames(self) -> list[str]:
        if self._app.session is None:
            return []
        return sorted({p.nickname for p in self._app.session.participants.values() if p.nickname})

    @staticmethod
    def _first_prefix_match(candidates: list[str], partial: str) -> str | None:
        partial_cf = partial.casefold()
        for candidate in candidates:
            if candidate.casefold().startswith(partial_cf):
                return candidate
        return None

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None

        if value.startswith("/"):
            if " " not in value:
                match = self._first_prefix_match(COMMANDS, value)
                return value + match[len(value):] if match else None

            command, _, rest = value.partition(" ")
            if command in ALIAS_ARG_COMMANDS and " " not in rest:
                match = self._first_prefix_match(self._alias_candidates(), rest)
                return value + match[len(rest):] if match else None
            return None

        mention = _MENTION_TAIL_RE.search(value)
        if mention:
            partial = mention.group(1)
            match = self._first_prefix_match(self._channel_nicknames(), partial)
            return value + match[len(partial):] if match else None

        return None
