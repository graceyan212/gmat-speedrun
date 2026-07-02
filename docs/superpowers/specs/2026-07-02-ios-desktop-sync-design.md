# GMAT Review — iOS ↔ Desktop Two-Way Sync

**Date:** 2026-07-02
**Status:** Design approved; ready for implementation plan
**Scope:** Two-way sync of review progress between the iOS app (`AnkiBridgeStub`, on a physical iPhone) and desktop Anki, using Anki's **existing** sync stack. No new sync protocol, and no changes to rslib's sync engine — this is wiring, not invention.

## 1. Goal
Review on the phone → shows on the desktop, and the reverse, with **no reviews lost or double-counted**. Offline review works and syncs when back online. Satisfies the assignment's "two apps, one engine, sync" requirement and challenge **7b** (sync test + conflict rule).

## 2. Approach (decision)
**Reuse Anki's built-in sync.** The assignment explicitly permits it. `rslib` already contains the full sync **client** (`SyncLogin`, `SyncStatus`, `SyncCollection`, `FullUploadOrDownload`) and a self-hostable HTTP **sync server** (`rslib/src/sync/http_server/`). We wire these up.

Rejected alternatives: **AnkiWeb** (external black box; harder to control the offline test; ToS concerns for a fork) and **a custom sync protocol** (correct merge / no-double-count is exactly what Anki's protocol already guarantees; infeasible to reproduce reliably in the timeline).

## 3. Architecture
Both apps are sync **clients** pointed at one self-hosted sync **server**, reached over an HTTPS tunnel:

```
 iPhone (real device) ──HTTPS──►  cloudflared tunnel ──► rslib sync server (Mac)
 Desktop Anki ────────HTTPS──────────────────────────►  (same server)
```

The server holds the authoritative collection per user; each client uploads its own changes and downloads the other's. Anki's protocol merges via the revlog, so both devices' reviews are applied and none are double-counted.

## 4. Components

### 4.1 Sync server (the hub)
- Run rslib's built-in Anki sync server on the Mac with a user account (env `SYNC_USER1=user:pass`), listening on a local port.
- Front it with **cloudflared** (`cloudflared tunnel --url http://localhost:PORT`) → a public `https://…` URL with a valid cert. Valid cert ⇒ no iOS ATS exception needed, and it works off local Wi-Fi.
- Quick-tunnel URLs are ephemeral (re-paste per session); a named cloudflared tunnel or Tailscale is the stable alternative if needed.

### 4.2 Desktop client
- Stock Anki sync — no code changes. Point it at the tunnel URL (Preferences → self-hosted sync server, or the `SYNC_ENDPOINT` env var), log in with the server credentials, sync.

### 4.3 iPhone client (the actual work)
- **Persist the collection:** stop deleting `collection.anki2` on launch; open the existing collection (create only if absent). Durable local state is a precondition for sync.
- **New C-ABI bridge wrappers** (protobuf marshalling stays in Rust, matching the existing wrappers):
  - `anki_sync_login(endpoint, user, pass, …)` → calls rslib `SyncLogin`, returns/stashes a `SyncAuth` (hkey + endpoint).
  - `anki_sync_collection(auth, …)` → calls rslib `SyncCollection`; handles the normal-sync path and the `FullUploadOrDownload` path when the server signals divergence.
- **UI:** a **"Sync" button** plus a text field to set the server URL (per-session tunnel URL). Surface sync status and errors.
- **Offline-safe:** reviews always write to the local collection regardless of connectivity; sync runs on demand (button) and/or on foreground when online; a failed sync (offline) leaves reviews queued locally and succeeds on the next attempt.

### 4.4 Conflict rule (challenge 7b) — inherited from Anki
- Normal changes merge via the revlog + change-tracking: both devices' reviews are applied, nothing is double-counted.
- On true divergence (can't reconcile incrementally — schema change or a failed sanity check), Anki forces a **full upload/download**: the side that uploads becomes authoritative and the other matches it.
- We **document** this and then **observe + record** the outcome in the 7b same-card test.

## 5. Prerequisite — DONE
**Step 0** — the app must install + run on a physical iPhone (code-signing). ✅ Completed 2026-07-02: automatic signing with the development team, `Apple Development` identity, `CODE_SIGNING_ALLOWED/REQUIRED=YES`. App runs on-device.

## 6. Data flow — first-sync sequencing
First sync is **not** symmetric and must be ordered:
1. Start the server + tunnel.
2. **Desktop** logs in and syncs first → seeds the server (full upload of the real collection).
3. **Phone** logs in and syncs → downloads that collection (full download).
4. Thereafter both sync incrementally, in either direction.

A naive "both sync near-empty collections" could let the phone overwrite the desktop — the plan must encode this order.

## 7. Testing / verification (7b)
- **Two-way basic:** review cards on the phone → sync → desktop shows them; and the reverse.
- **7b no-loss / no-double:** 10 cards reviewed offline on the phone + 10 different offline on the desktop → reconnect → sync both → confirm all 20 land exactly once (revlog count / due states).
- **7b conflict:** the **same** card reviewed on both devices offline → sync → observe + document the winner (per §4.4).
- **Offline:** airplane-mode a review on the phone, confirm it's recorded locally, then sync succeeds when back online.
- **Proof artifact:** a recording of a card reviewed on the phone appearing on the desktop after sync.

## 8. Out of scope (YAGNI)
- **Media sync** (`SyncMedia`) — the deck has no meaningful media; collection/review sync covers the requirement.
- AnkiWeb; a custom sync protocol; automatic real-time background sync (manual "Sync" + optional sync-on-foreground is enough).

## 9. Risks
- The two `anki_sync_*` wrappers are the main new native code; login → auth → collection is a small state machine to get right.
- First-sync sequencing (§6) must be followed, or the phone's fresh collection could clobber the desktop's.
- cloudflared quick-tunnel URLs change per session; use a named tunnel if a stable URL is needed.
- Free Apple ID provisioning expires after 7 days (fine for the submission window; re-run from Xcode to refresh).
