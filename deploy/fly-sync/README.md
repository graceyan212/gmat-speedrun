# Deploy the GMAT sync server to fly.io

Hosts your fork's `anki-sync-server` on a public HTTPS URL so the **desktop app and
the iPhone app sync through it** — two-way, and each keeps a local copy so review
still works **offline** (sync when back online).

## One-time setup (you run these — they need your fly.io account)

```bash
brew install flyctl                 # or: curl -L https://fly.io/install.sh | sh
fly auth login                      # or `fly auth signup` for a new account

# Make sure fly can fetch your fork's current code:
git -C ~/Desktop/alpha/speedrun/anki push origin main

cd ~/Desktop/alpha/speedrun/deploy/fly-sync
fly launch --no-deploy              # accept the app name/region, or edit fly.toml
fly volumes create anki_data --size 1 --region <your-region>
fly secrets set SYNC_USER1="demo:CHOOSE_A_PASSWORD"   # your sync login
fly deploy
```

`fly deploy` prints your URL, e.g. **`https://gmat-sync.fly.dev`**. Sanity check:

```bash
curl -sS https://gmat-sync.fly.dev/health || fly logs
```

## Point the two apps at it

**Desktop (Anki fork):** Preferences → *Syncing* → enable **self-hosted sync server**,
URL = `https://gmat-sync.fly.dev/` → click **Sync**, log in with `demo` / your password.
First sync uploads your collection.

**iPhone:** in the app's sync settings, set the same endpoint + `demo` / password, tap
**Sync**. (The iOS bridge already has `login` / `full_upload_or_download` / `collection`
wrappers.)

## Prove the two-way round-trip (this is the Jul-3 deliverable)

1. On the **phone**, review a card → **Sync**.
2. On the **desktop**, **Sync** → the review you just did shows up. ✅
3. Do a review on **desktop** → **Sync**; on the **phone** → **Sync** → it appears. ✅
4. (Offline check) Turn on Airplane Mode, review on the phone, turn it back on, **Sync**.

Capture screenshots/logs of steps 2 & 3 — that's your proof.

## Notes

- **Version match matters.** The Dockerfile builds the server **from your fork** so its
  sync protocol matches your clients. If you see a "client/server version" error, the
  fork branch fly built from is out of date — push and `fly deploy` again.
- **Cost:** a `shared-cpu-1x`/512 MB machine that scales to zero is within fly's small
  free-ish tier; the 1 GB volume is negligible. Fine for a demo.
- **Credentials:** `SYNC_USER1="user:pass"` is a fly *secret*, not in git. Add more users
  with `SYNC_USER2`, etc.
