# GMAT sync server — one command, zero pasting

Hosts your fork's `anki-sync-server` on a **stable** public HTTPS URL so the
desktop app and the iPhone app sync through it — two-way, and each keeps a local
copy so review still works **offline** (sync when back online).

**The apps no longer ask you to paste a URL/username/password.** They ship with
the values below baked in as defaults, so a fresh install syncs with **one tap**.
The `provision.py` script is the single source of truth: it resolves/deploys the
server, ensures the default account exists, and writes those defaults to
`sync-config.json`. See "How the apps default to it" at the bottom.

## The stable defaults (baked into both apps)

| | value |
|---|---|
| Server URL | `https://gmat-sync.fly.dev/` |
| Username | `demo` |
| Password | `gmatsync2026` |

Why these are stable: the fly.io **app name** `gmat-sync` fixes the hostname
`gmat-sync.fly.dev` for the life of the app (fly only frees the name if the app
is destroyed). The server hashes `SYNC_USER1="user:pass"` with a **fixed salt**
(`rslib/src/sync/http_server/mod.rs`), so the same `demo:gmatsync2026` logs in on
every redeploy. Nothing to re-paste, ever.

## One command

```bash
python3 deploy/fly-sync/provision.py --target fly --deploy
```

That single command:
1. pushes your `anki` fork (so fly builds from current sync-protocol code),
2. sets the default account (`SYNC_USER1=demo:gmatsync2026`),
3. `fly deploy`s the `gmat-sync` app,
4. verifies the server answers,
5. writes `deploy/fly-sync/sync-config.json` — the config both apps default to.

Prereqs (one-time): `brew install flyctl && fly auth login`.

Just refresh the config without deploying (uses the stable hostname):

```bash
python3 deploy/fly-sync/provision.py --target fly            # writes + health-checks
python3 deploy/fly-sync/provision.py --target fly --no-verify  # writes only
```

### No fly.io billing? Run it free & local

If the fly trial is paused (needs a card) or you want a fully offline demo, run
the fork's own sync server on your Mac — same default account, same config shape,
no fly account:

```bash
python3 deploy/fly-sync/provision.py --target local --run
```

This builds + starts `anki-sync-server`, then writes a LAN URL
(`http://<your-ip>:27701/`) into `sync-config.json`. Point both apps at that URL
(the phone must be on the same Wi-Fi). Verified working: the server registers the
`demo` account and answers on the port.

## Prove the two-way round-trip (the Jul-3 deliverable)

1. On the **phone**, review a card → **Sync** (one tap; already configured).
2. On the **desktop**, **Sync** → the review you just did shows up. ✅
3. Do a review on **desktop** → **Sync**; on the **phone** → **Sync** → it appears. ✅
4. (Offline) Airplane Mode, review on the phone, turn it back on, **Sync**.

First sync is ordered: **desktop syncs first** (uploads the real collection),
then the phone syncs (downloads it). After that, either direction, incrementally.

## How the apps default to it (no paste)

Both apps read the three values from the table above as build-time defaults.
`sync-config.json` is the single source of truth; the app-side defaults must
match it. If you change the URL/account, re-run `provision.py` and update the two
default constants below to the new values.

**iPhone (`ios/AnkiBridgeStub/AnkiBridgeStub/ContentView.swift`)** —
`ReviewViewModel` currently defaults each field to `""`. Change the three
`UserDefaults` fallbacks so a first launch (no saved value) uses the defaults:

```swift
@Published var serverURL = UserDefaults.standard.string(forKey: "syncServerURL") ?? "https://gmat-sync.fly.dev/" { … }
@Published var syncUser  = UserDefaults.standard.string(forKey: "syncUser")      ?? "demo"                        { … }
@Published var syncPass  = UserDefaults.standard.string(forKey: "syncPass")      ?? "gmatsync2026"                { … }
```

`hasCredentials` then returns true on first launch, so the header **SYNC** chip
syncs immediately with one tap and `autoSync()` runs on launch/after answers —
no sheet, no typing. (Double-tapping SYNC still opens the sheet to override.)

**Desktop (`anki/qt/aqt/profiles.py`)** — the sync endpoint is
`sync_endpoint()` → `_current_sync_url() or custom_sync_url() or None`, and
`custom_sync_url()` is just `self.profile.get("customSyncUrl")`. Change that one
getter so an unset profile defaults to the server (the ONE required edit):

```python
def custom_sync_url(self) -> str | None:            # profiles.py ~line 719
    """A custom server provided by the user (defaults to the GMAT sync server)."""
    return self.profile.get("customSyncUrl") or "https://gmat-sync.fly.dev/"
```

Optional username prefill (nicety, not required): the login dialog is opened via
`sync_login(self.mw, on_success)` with an empty username, and
`get_id_and_pass_from_user` (`qt/aqt/sync.py` ~line 349) does `user.setText(username)`.
To pre-fill `demo`, pass it through — in `qt/aqt/preferences.py`'s `sync_login`
call `sync_login(self.mw, on_success, self.prof.get("syncUser", "demo"))`.

The password is **not** stored on the desktop (Anki keeps only the derived
`syncKey` after the first login), so the desktop needs the password typed **once**
in the Sync dialog — `demo` / `gmatsync2026`. After that the saved `syncKey` makes
every later sync one-click, and the URL is never pasted. (This one-time password
entry is inherent to Anki's login flow; baking a plaintext password into the
profile isn't how that flow is wired.)

> Owner note: the Swift files and `qt/aqt/*.py` are edited by the main agent, not
> by the sync-provisioning work — these snippets are the exact, minimal change to
> apply there.

## Notes

- **Version match matters.** The Dockerfile builds the server **from your fork**
  so its sync protocol matches your clients. A "client/server version" error means
  the branch fly built from is stale — push and re-run `provision.py --deploy`.
- **Cost:** a `shared-cpu-1x`/512 MB machine that scales to zero + a 1 GB volume
  is tiny. The fly free trial requires a card to be added once it ends.
- **Credentials:** the default `demo` account is fine for this self-hosted demo.
  Rotate/add users by editing `provision.py`'s `DEFAULT_*` constants (and the two
  app defaults above), or add `SYNC_USER2`, etc.
