# bitchat-tui

A terminal client for [bitchat](https://github.com/permissionlesstech/bitchat)'s public
**location channels** (geohash-based chat rooms), connecting over Tor to the same Nostr
relays real bitchat apps use — so messages you post here are visible to real bitchat
users in that geohash, and vice versa.

Built directly from bitchat's own Nostr transport source (event kinds, tag formats, relay
selection, per-channel key derivation) for real interop, not a simulation.

> Built with the help of [Claude](https://claude.com/claude-code) (Anthropic's AI assistant).
> See [Contributing](#contributing) for this project's stance on AI-assisted PRs.

## Scope

- ✅ Public location channels (`g` tag, kinds `20000`/`20001`) — join, chat, see who's present.
- ✅ Real end-to-end encrypted peer-to-peer DMs (NIP-17/NIP-59, real standard NIP-44 v2 —
  verified against the official test vectors) between **bitchat-tui users** who've exchanged
  npubs. Not the same thing as bitchat's own "favorites" DMs (see caveat below).
- ✅ `@mention` highlighting, per-user color coding, and a local blocklist.
- ❌ Private DMs to a real bitchat mobile app's "favorites" — bitchat requires a prior
  Bluetooth mesh encounter to establish that trust relationship; there's no way to bootstrap
  it from an internet-only client. bitchat-tui's own DM feature is a separate, real NIP-17
  implementation that works between bitchat-tui (or other NIP-17 client) users directly —
  it does not use bitchat's Bluetooth-pairing bootstrap and so won't reach real bitchat
  mobile users, only other holders of an npub you've added as a contact.

## Requirements

- Tor running locally with its SOCKS proxy on `127.0.0.1:9050` (default). On Ubuntu:
  ```
  sudo systemctl start tor
  ```
- conda (this project was set up with a conda env named `bitchat-tui`, Python 3.11).

## Setup

The env already exists on this machine as `bitchat-tui`. To recreate it elsewhere:

```
conda env create -f environment.yml
```

## Run

```
conda run -n bitchat-tui python -m bitchat_tui
```

or activate the env first (`conda activate bitchat-tui`) and run `python -m bitchat_tui`.

First run asks for a nickname, then joins your last-used geohash channel (default `9q5`,
a coarse US-west region — pick your own with `/join`).

## Standalone binary

A prebuilt single-file Linux binary lives at `dist/bitchat-tui` (~30 MB, no
Python/conda needed to run it — verified working with Tor's SOCKS proxy and
conda both absent from `PATH`):

```
./dist/bitchat-tui
```

To rebuild it after code changes, run:

```
./scripts/build_binary.sh
```

(This installs PyInstaller into the `bitchat-tui` env if missing, builds, and cleans up
`build/`. `bitchat-tui.spec` records the build config; `dist/` holds the output; add both
`build/` and `dist/` to VCS ignores if this becomes a git repo.)

## Interface

- **Autocomplete**: press **→** or **End** to accept an inline ghosted suggestion while
  typing — works for `/commands`, the alias/nickname argument of `/dm`, `/block`, `/unblock`
  and `/npub`, and `@mentions` anywhere in a message.
- **Resizable panels**: click-drag the highlighted bar between the chat area and the
  sidebar (or between the chat log and the status log) to resize them.
- **Copy text**: click-drag to select text in-app, then **Ctrl+C** to copy it (works over
  SSH too, via the terminal's OSC52 clipboard protocol — most terminals support this except
  macOS Terminal.app). Alternatively hold **Shift** while selecting to bypass the app
  entirely and use your terminal's own native selection/copy.
- **Spam filter**: the sidebar button toggles duplicate/flood suppression on or off. When
  on, a sender repeating the same message more than 3 times in 30s, or posting more than 5
  messages in 10s, has the excess silently dropped (applies to both channel messages and
  DMs) — it doesn't affect what they see, only what shows up in your client.

## In-app commands

**Channel**
- `/join <geohash>` — switch channel. Precision guide: `2`=region, `4`=province, `5`=city,
  `6`=neighborhood, `7`=block (look up a geohash for your area at geohash.org).
- `/nick <name>` — change your display nickname.
- `@nickname` anywhere in a message — mentions that participant (tags them + highlights the
  message for them). Each participant gets a consistent color, derived from their pubkey.

**Direct messages** (real E2E encryption between bitchat-tui users, see Scope above)
- `/whoami` — show your persistent DM npub (share this so people can add you) and your
  current channel's (separate, unlinkable) identity.
- `/addcontact <alias> <npub-or-hex>` — save someone's DM npub under a short name.
- `/dm <alias-or-npub> <message>` — send them a private, end-to-end encrypted message.
- `/npub <alias-or-channel-nickname>` — look up someone's npub directly (their DM identity
  if it's a saved contact, their channel identity if they're active in the current channel,
  or both if it matches both) without sending anything.
- `/contacts` — list saved contacts.

**Moderation**
- `/block <alias-or-channel-nickname-or-npub>` — stop showing someone's messages. If the
  name matches someone currently active in the channel, blocks their channel identity
  (geohash identities are unlinkable across channels, so this doesn't follow them
  elsewhere); otherwise blocks a contact's/npub's DM identity. A blocked person vanishes
  from the participant list, but their nickname is remembered so `/unblock <name>` still
  works afterward.
- `/unblock <...>` / `/blocked` — undo / list (with full npubs).

`/quit` exits. Anything else you type is sent as a public chat message to the current channel.

## Layout

```
bitchat_tui/
  crypto.py     identity: per-geohash + persistent device secp256k1 keys
  geohash.py    geohash encode/decode
  nip44.py      NIP-44 v2 encryption (verified against the official test vectors)
  nip17.py      NIP-17 DM rumor/seal + NIP-59 gift wrap, built on nip44
  nostr.py      Nostr event construction/signing + relay websocket client (over Tor)
  relays.py     bitchat's default relay list + geo-indexed relay directory
  channel.py    public channel session: relay selection, subscriptions, presence, mentions
  dm.py         private DM session: gift-wrap subscribe/send
  contacts.py   alias -> DM npub address book
  blocklist.py  blocked pubkeys (pubkey -> remembered label)
  suggest.py    inline autocomplete for /commands, aliases, and @mentions
  spam.py       per-sender duplicate/flood suppression
  resizer.py    draggable divider widget for resizable panels
  app.py        Textual TUI
scripts/
  build_binary.sh   rebuilds dist/bitchat-tui
```

## Contributing

This project was built with the help of Claude (Anthropic's AI assistant), and AI-assisted
pull requests are welcome. That said, please don't spam low-effort/unreviewed AI-generated
PRs — if you use an AI tool, read and understand the diff before opening it, make sure it
actually runs, and describe what it does and why in your own words.
