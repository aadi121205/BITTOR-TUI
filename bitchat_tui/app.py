"""Textual TUI for browsing bitchat's public geohash location channels over Tor,
plus real end-to-end encrypted peer-to-peer DMs (NIP-17/NIP-59) between bitchat-tui users.
"""

from __future__ import annotations

import time

from rich.markup import escape as rich_escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, ListItem, ListView, Label, RichLog

from . import blocklist, config, contacts, crypto, geohash, nip17, nostr
from .channel import ChannelSession, ChatMessage, MENTION_RE
from .dm import DMSession
from .resizer import ResizeHandle
from .spam import SpamFilter
from .suggest import ChatSuggester

WELCOME = (
    "[b]bitchat-tui[/b] — browsing bitchat's public location channels over Tor.\n"
    "Type a message and press Enter to post. [b]@nickname[/b] mentions someone in the channel "
    "(highlighted for them). Each participant gets a consistent color. Press "
    "[b]→[/b] or [b]End[/b] to accept a ghosted [b]/command[/b] or [b]@alias[/b] suggestion.\n"
    "Drag the highlighted bars to resize the sidebar / status panel. To copy text: click-drag "
    "to select it in-app then press [b]Ctrl+C[/b] (works over SSH too), or hold [b]Shift[/b] "
    "while selecting to use your terminal's own native copy instead.\n"
    "Channel commands: [b]/join <geohash>[/b], [b]/nick <name>[/b].\n"
    "DM commands: [b]/whoami[/b] (show your DM npub), [b]/addcontact <alias> <npub>[/b], "
    "[b]/dm <alias> <message>[/b], [b]/npub <alias>[/b] (look up someone's npub), [b]/contacts[/b].\n"
    "Moderation: [b]/block <alias-or-nickname-or-npub>[/b], [b]/unblock <...>[/b], [b]/blocked[/b].\n"
    "[b]/quit[/b] to exit. Geohash precision: 2=region 4=province 5=city 6=neighborhood 7=block.\n"
)

# Deterministic per-user colors (IRC/bitchat-style) -- green and red are reserved for
# "you"/errors, yellow is reserved for @mention emphasis, so those are excluded here.
_USER_COLOR_PALETTE = [
    "cyan", "magenta", "blue", "bright_cyan", "bright_blue", "bright_magenta",
    "orange3", "turquoise2", "plum2", "gold3", "deep_sky_blue1", "medium_purple2",
    "sky_blue2", "light_slate_blue",
]


def _short(pubkey_hex: str) -> str:
    return pubkey_hex[:8]


def _user_color(pubkey_hex: str) -> str:
    idx = int(pubkey_hex[:8], 16) % len(_USER_COLOR_PALETTE)
    return _USER_COLOR_PALETTE[idx]


def _render_content(text: str) -> str:
    """Escape untrusted chat content for a markup=True RichLog, then highlight @mentions."""
    escaped = rich_escape(text)
    return MENTION_RE.sub(lambda m: f"[b reverse]@{m.group(1)}[/b reverse]", escaped)


class BitchatTUIApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #body {
        height: 1fr;
    }
    #chat-col {
        width: 1fr;
    }
    #side-col {
        width: 24;
        border-left: solid $accent;
    }
    #chat-log {
        height: 1fr;
        border: solid $accent;
    }
    #status-log {
        height: 6;
        border-top: solid $accent-darken-1;
        color: $text-muted;
    }
    #participants-label {
        padding: 0 1;
        text-style: bold;
    }
    #participants {
        height: 1fr;
    }
    #spam-filter-toggle {
        dock: bottom;
        width: 100%;
        min-width: 0;
    }
    #msg-input {
        dock: bottom;
    }
    """

    BINDINGS = [("ctrl+q", "quit", "Quit")]

    def __init__(self):
        super().__init__()
        self.cfg = config.load()
        self.seed = crypto.load_or_create_device_seed()
        self.device_identity = crypto.load_or_create_device_identity()
        self.blocked = blocklist.load()
        self.spam_filter = SpamFilter()
        self.session: ChannelSession | None = None
        self.dm_session: DMSession | None = None
        self.awaiting_nickname = not bool(self.cfg.get("nickname"))
        self._shutting_down = False
        self._side_col_width = 24  # cells; must match #side-col's CSS width above
        self._status_log_height = 6  # cells; must match #status-log's CSS height above

    def _is_blocked(self, pubkey_hex: str) -> bool:
        return pubkey_hex in self.blocked

    def _adjust_side_col_width(self, delta: int) -> None:
        # Handle sits left of #side-col: dragging it left (negative delta) should grow
        # the sidebar, dragging right (positive delta) should shrink it -- hence the minus.
        self._side_col_width = max(14, min(70, self._side_col_width - delta))
        self.query_one("#side-col").styles.width = self._side_col_width

    def _adjust_status_log_height(self, delta: int) -> None:
        # Handle sits above #status-log: dragging it down (positive delta) grows it.
        self._status_log_height = max(3, min(25, self._status_log_height + delta))
        self.query_one("#status-log").styles.height = self._status_log_height

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="chat-col"):
                yield RichLog(id="chat-log", wrap=True, markup=True, highlight=False)
                yield ResizeHandle("horizontal", self._adjust_status_log_height, id="log-handle")
                yield RichLog(id="status-log", wrap=True, markup=True)
            yield ResizeHandle("vertical", self._adjust_side_col_width, id="side-handle")
            with Vertical(id="side-col"):
                yield Label("participants (5m)", id="participants-label")
                yield ListView(id="participants")
                yield Button("Spam filter: ON", id="spam-filter-toggle", variant="success")
        yield Input(placeholder="…", id="msg-input", suggester=ChatSuggester(self))
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "spam-filter-toggle":
            return
        self.spam_filter.enabled = not self.spam_filter.enabled
        if self.spam_filter.enabled:
            event.button.label = "Spam filter: ON"
            event.button.variant = "success"
        else:
            event.button.label = "Spam filter: OFF"
            event.button.variant = "error"
        self.query_one("#status-log", RichLog).write(
            f"spam filter {'enabled' if self.spam_filter.enabled else 'disabled'}"
        )

    async def on_mount(self) -> None:
        self.title = "bitchat-tui"
        chat_log = self.query_one("#chat-log", RichLog)
        chat_log.write(WELCOME)

        status = self.query_one("#status-log", RichLog)
        status.write("checking Tor SOCKS proxy at 127.0.0.1:9050…")
        reachable = await nostr.check_tor_socks()
        if not reachable:
            status.write(
                "[red]Tor SOCKS proxy not reachable.[/red] Start it with: "
                "sudo systemctl start tor  (then restart this app)"
            )
            chat_log.write("[red]Cannot continue without Tor running. Ctrl+Q to quit.[/red]")
            return
        status.write("[green]Tor reachable.[/green]")

        self.dm_session = DMSession(
            identity=self.device_identity,
            on_dm=self._handle_dm,
            on_status=self._handle_status,
            is_blocked=self._is_blocked,
            spam_filter=self.spam_filter,
        )
        self.run_worker(self.dm_session.start(), exclusive=False, exit_on_error=False)

        input_widget = self.query_one("#msg-input", Input)
        if self.awaiting_nickname:
            input_widget.placeholder = "choose a nickname and press Enter"
            chat_log.write("Pick a nickname to get started.")
        else:
            input_widget.placeholder = "message… (or /join <geohash>, /nick <name>)"
            await self._join_channel(self.cfg["last_geohash"])
        input_widget.focus()

    async def _join_channel(self, gh: str) -> None:
        gh = gh.strip().lower()
        chat_log = self.query_one("#chat-log", RichLog)
        status = self.query_one("#status-log", RichLog)

        if not geohash.is_valid(gh):
            chat_log.write(f"[red]'{gh}' is not a valid geohash (1-12 chars, base32).[/red]")
            return

        if self.session is not None:
            status.write(f"leaving '{self.session.geohash}'…")
            await self.session.stop()
            self.session = None

        chat_log.write(f"[b]joining geohash channel '{gh}'…[/b]")
        self.session = ChannelSession(
            seed=self.seed,
            geohash=gh,
            nickname=self.cfg["nickname"],
            on_message=self._handle_chat_message,
            on_participants_changed=self._handle_participants_changed,
            on_status=self._handle_status,
            is_blocked=self._is_blocked,
            spam_filter=self.spam_filter,
        )
        chat_log.write(f"your identity for this channel: {self.session.identity.npub}")
        await self.session.start()
        config.save({"last_geohash": gh})
        self.title = f"bitchat-tui — {gh}"

    async def _handle_status(self, message: str) -> None:
        if self._shutting_down:
            return
        self.query_one("#status-log", RichLog).write(message)

    async def _handle_chat_message(self, msg: ChatMessage) -> None:
        if self._shutting_down:
            return
        chat_log = self.query_one("#chat-log", RichLog)
        ts = time.strftime("%H:%M:%S", time.localtime(msg.created_at))
        who = rich_escape(msg.nickname or _short(msg.pubkey))
        style = "b green" if msg.is_own else ("b yellow" if msg.mentions_me else f"b {_user_color(msg.pubkey)}")
        line = f"[{ts}] [{style}]{who}[/{style}]: {_render_content(msg.content)}"
        if msg.mentions_me and not msg.is_own:
            line = f"[on grey15]{line}[/on grey15]"
        chat_log.write(line)

    async def _handle_dm(self, dm: nip17.UnwrappedDM) -> None:
        if self._shutting_down:
            return
        chat_log = self.query_one("#chat-log", RichLog)
        ts = time.strftime("%H:%M:%S", time.localtime(dm.created_at))
        alias = contacts.alias_for(dm.sender_pubkey_hex) or _short(dm.sender_pubkey_hex)
        color = _user_color(dm.sender_pubkey_hex)
        chat_log.write(
            f"[{ts}] [b magenta]DM from[/b magenta] [b {color}]{rich_escape(alias)}[/b {color}]: {_render_content(dm.content)}"
        )

    async def _handle_participants_changed(self) -> None:
        if self._shutting_down or self.session is None:
            return
        listview = self.query_one("#participants", ListView)
        await listview.clear()
        for p in self.session.online_participants():
            name = rich_escape(p.nickname) if p.nickname else _short(p.pubkey)
            color = _user_color(p.pubkey)
            await listview.append(ListItem(Label(f"[{color}]{name}[/{color}]")))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return

        if self.awaiting_nickname:
            self.cfg["nickname"] = text
            config.save({"nickname": text})
            self.awaiting_nickname = False
            event.input.placeholder = "message… (or /join <geohash>, /nick <name>)"
            self.query_one("#chat-log", RichLog).write(f"nickname set to [b]{text}[/b]")
            await self._join_channel(self.cfg["last_geohash"])
            return

        if text.startswith("/join "):
            await self._join_channel(text[len("/join "):])
            return
        if text.startswith("/nick "):
            new_nick = text[len("/nick "):].strip()
            if new_nick:
                self.cfg["nickname"] = new_nick
                config.save({"nickname": new_nick})
                if self.session:
                    self.session.nickname = new_nick
                self.query_one("#chat-log", RichLog).write(f"nickname changed to [b]{new_nick}[/b]")
            return
        if text in ("/quit", "/exit"):
            self.exit()
            return
        if text == "/whoami":
            chat_log = self.query_one("#chat-log", RichLog)
            chat_log.write(f"your DM npub (share this so people can message you): [b]{self.device_identity.npub}[/b]")
            if self.session:
                chat_log.write(f"your identity in this channel: {self.session.identity.npub}")
            return
        if text == "/contacts":
            chat_log = self.query_one("#chat-log", RichLog)
            book = contacts.load()
            if not book:
                chat_log.write("no contacts saved yet — use /addcontact <alias> <npub>")
            else:
                for alias, pk in book.items():
                    chat_log.write(f"  {rich_escape(alias)}: {pk[:16]}…")
            return
        if text.startswith("/addcontact "):
            await self._handle_addcontact(text[len("/addcontact "):])
            return
        if text.startswith("/dm "):
            await self._handle_dm_command(text[len("/dm "):])
            return
        if text.startswith("/npub "):
            self._handle_npub_command(text[len("/npub "):].strip())
            return
        if text.startswith("/block "):
            await self._handle_block_command(text[len("/block "):].strip(), block=True)
            return
        if text.startswith("/unblock "):
            await self._handle_block_command(text[len("/unblock "):].strip(), block=False)
            return
        if text == "/blocked":
            chat_log = self.query_one("#chat-log", RichLog)
            if not self.blocked:
                chat_log.write("no one is blocked")
            else:
                for pk, label in sorted(self.blocked.items(), key=lambda kv: kv[1].lower()):
                    chat_log.write(f"  {rich_escape(label)}: {crypto.pubkey_hex_to_npub(pk)}")
            return

        if self.session is None:
            self.query_one("#chat-log", RichLog).write("[red]not connected to a channel yet[/red]")
            return
        await self.session.send_message(text)

    def _resolve_contact_or_npub(self, target: str) -> str | None:
        """alias (contacts) -> raw npub/hex. Used for DMs: only a saved contact's persistent
        DM identity (or a directly-pasted npub) is valid -- a live channel nickname is NOT,
        since that's a different, per-geohash, unlinkable key that isn't listening for DMs."""
        book = contacts.load()
        pubkey_hex = book.get(target.lower())
        if pubkey_hex:
            return pubkey_hex
        try:
            return crypto.resolve_pubkey_hex(target)
        except ValueError:
            return None

    def _resolve_block_target(self, target: str) -> tuple[str, str] | None:
        """Returns (pubkey_hex, scope) or None. scope is "channel" or "contact/npub".

        Checks the CURRENT channel's participants first: a channel nickname and a
        contacts alias are different namespaces that can collide on the same word
        (e.g. contact "bob" the real DM friend vs. some stranger who typed nickname
        "bob" in this geohash) but refer to unrelated keys -- geohash identities are
        deliberately unlinkable to a device's persistent DM identity. Since /block is
        almost always a reaction to something just posted in the channel, that scope
        takes priority; /addcontact + /block <alias> still works for blocking DMs.
        """
        if self.session is not None:
            for p in self.session.participants.values():
                if p.nickname and p.nickname.lower() == target.lower():
                    return p.pubkey, "channel"
        pubkey_hex = self._resolve_contact_or_npub(target)
        if pubkey_hex:
            return pubkey_hex, "contact/npub"
        return None

    async def _handle_block_command(self, target: str, block: bool) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        if not target:
            chat_log.write(f"[red]usage: /{'block' if block else 'unblock'} <alias-or-nickname-or-npub>[/red]")
            return

        if block:
            resolved = self._resolve_block_target(target)
            if resolved is None:
                chat_log.write(f"[red]couldn't resolve '{rich_escape(target)}' to a contact, channel nickname, or npub[/red]")
                return
            pubkey_hex, scope = resolved
            scope_note = (
                "this channel only — geohash identities are unlinkable across channels"
                if scope == "channel"
                else "their DM identity — does not affect a different per-channel nickname"
            )
            self.blocked[pubkey_hex] = target
            blocklist.save(self.blocked)
            if self.session and pubkey_hex in self.session.participants:
                del self.session.participants[pubkey_hex]
            chat_log.write(f"[b red]blocked[/b red] {rich_escape(target)} ({pubkey_hex[:16]}…) — {scope_note}")
        else:
            # Someone already blocked no longer shows up as a channel participant (that's
            # the block working), so check the blocklist's own remembered labels first --
            # falling back to _resolve_block_target only covers labels we never stored.
            pubkey_hex = next(
                (pk for pk, label in self.blocked.items() if label.lower() == target.lower()), None
            )
            if pubkey_hex is None:
                resolved = self._resolve_block_target(target)
                pubkey_hex = resolved[0] if resolved else None
            if pubkey_hex is None or pubkey_hex not in self.blocked:
                chat_log.write(f"[red]'{rich_escape(target)}' isn't currently blocked[/red]")
                return
            del self.blocked[pubkey_hex]
            blocklist.save(self.blocked)
            chat_log.write(f"[b]unblocked[/b] {rich_escape(target)} ({pubkey_hex[:16]}…)")
        if self.session:
            await self._handle_participants_changed()

    async def _handle_addcontact(self, rest: str) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        parts = rest.strip().split(None, 1)
        if len(parts) != 2:
            chat_log.write("[red]usage: /addcontact <alias> <npub-or-hex-pubkey>[/red]")
            return
        alias, value = parts
        try:
            pubkey_hex = crypto.resolve_pubkey_hex(value)
        except ValueError as exc:
            chat_log.write(f"[red]{exc}[/red]")
            return
        contacts.add(alias, pubkey_hex)
        chat_log.write(f"saved contact [b]{rich_escape(alias)}[/b] -> {pubkey_hex[:16]}…")

    def _handle_npub_command(self, target: str) -> None:
        """Resolve an alias/nickname straight to its npub(s), no DM sent."""
        chat_log = self.query_one("#chat-log", RichLog)
        if not target:
            chat_log.write("[red]usage: /npub <alias-or-nickname-or-npub-or-hex>[/red]")
            return

        found = False
        book = contacts.load()
        contact_pubkey = book.get(target.lower())
        if contact_pubkey:
            chat_log.write(f"[b]{rich_escape(target)}[/b] (contact, DM identity): {crypto.pubkey_hex_to_npub(contact_pubkey)}")
            found = True

        if self.session is not None:
            for p in self.session.participants.values():
                if p.nickname and p.nickname.lower() == target.lower():
                    chat_log.write(f"[b]{rich_escape(target)}[/b] (this channel): {crypto.pubkey_hex_to_npub(p.pubkey)}")
                    found = True

        if not found:
            try:
                pubkey_hex = crypto.resolve_pubkey_hex(target)
                chat_log.write(f"npub: [b]{crypto.pubkey_hex_to_npub(pubkey_hex)}[/b]  hex: {pubkey_hex}")
            except ValueError:
                chat_log.write(f"[red]'{rich_escape(target)}' isn't a known contact, a nickname in this channel, or an npub/hex pubkey[/red]")

    async def _handle_dm_command(self, rest: str) -> None:
        chat_log = self.query_one("#chat-log", RichLog)
        parts = rest.strip().split(None, 1)
        if len(parts) != 2:
            chat_log.write("[red]usage: /dm <alias-or-npub> <message>[/red]")
            return
        target, message = parts
        pubkey_hex = self._resolve_contact_or_npub(target)
        if pubkey_hex is None:
            chat_log.write(
                f"[red]unknown contact '{rich_escape(target)}' — /addcontact first, or pass an npub directly[/red]"
            )
            return
        if self.dm_session is None:
            chat_log.write("[red]DMs not ready yet (still connecting to relays over Tor)[/red]")
            return
        try:
            await self.dm_session.send_dm(pubkey_hex, message)
        except Exception as exc:
            chat_log.write(f"[red]failed to send DM: {exc}[/red]")
            return
        ts = time.strftime("%H:%M:%S")
        chat_log.write(f"[{ts}] [b magenta]DM to {rich_escape(target)}[/b magenta]: {_render_content(message)}")

    async def on_unmount(self) -> None:
        self._shutting_down = True
        if self.session is not None:
            await self.session.stop()
        if self.dm_session is not None:
            await self.dm_session.stop()


def run() -> None:
    BitchatTUIApp().run()


if __name__ == "__main__":
    run()
