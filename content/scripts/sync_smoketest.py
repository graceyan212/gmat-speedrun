#!/usr/bin/env python3
"""Sync-server round-trip smoketest.

De-risking step for the iOS <-> desktop sync bridge: proves that Anki's
self-hostable sync server (`python -m anki.syncserver`) actually round-trips
data between two independent collections before any bridge/iOS code is
written.

Flow:
  - Collection A (fresh temp dir): add one note, then sync against the
    server. Against a brand-new server + user this is a FULL_UPLOAD.
  - Collection B (fresh, separate temp dir): sync against the same server.
    It must RECEIVE A's note (a FULL_DOWNLOAD).

Success = B ends with note_count() >= 1 and "ROUND-TRIP OK" is printed.

Usage (server must already be running - see docs/superpowers/plans/
2026-07-02-ios-desktop-sync-plan.md Task 1, Step 1):

    PYTHONPATH=/Users/graceyan/Desktop/alpha/speedrun/anki/out/pylib:\
/Users/graceyan/Desktop/alpha/speedrun/anki/out/qt \
    /Users/graceyan/Desktop/alpha/speedrun/anki/out/pyenv/bin/python \
    content/scripts/sync_smoketest.py

NOTE on the real API (discovered by introspecting col._backend; differs
slightly from a first guess at the RPC shapes):
  - col._backend.sync_login(message: SyncLoginRequest) -> SyncAuth
    i.e. it takes ONE positional protobuf request message, not username=/
    password=/endpoint= kwargs.
  - col._backend.sync_collection(*, auth: SyncAuth, sync_media: bool)
    -> SyncCollectionResponse
    This one DOES take kwargs directly (auth=, sync_media=).
  - col._backend.full_upload_or_download(message: FullUploadOrDownloadRequest)
    -> generic_pb2.Empty
    Also takes ONE positional protobuf request message (auth/upload/
    server_usn are fields on the request, not kwargs to the method).
  - SyncCollectionResponse.required is a
    SyncCollectionResponse.ChangesRequired enum: NO_CHANGES=0,
    NORMAL_SYNC=1, FULL_SYNC=2, FULL_DOWNLOAD=3, FULL_UPLOAD=4.
"""

import os
import tempfile

from anki.collection import Collection
import anki.sync_pb2 as pb

ENDPOINT = "http://127.0.0.1:8080/"  # trailing slash required by the client
USER, PASS = "demo", "demo"

ChangesRequired = pb.SyncCollectionResponse.ChangesRequired


def open_col() -> Collection:
    d = tempfile.mkdtemp(prefix="anki-sync-smoketest-")
    return Collection(os.path.join(d, "c.anki2"))


def login(col: Collection) -> pb.SyncAuth:
    req = pb.SyncLoginRequest(username=USER, password=PASS, endpoint=ENDPOINT)
    return col._backend.sync_login(req)


def sync(col: Collection, auth: pb.SyncAuth) -> str:
    """Run one sync step, performing a full upload/download if required.

    Returns the human-readable ChangesRequired name for logging.
    """
    out = col._backend.sync_collection(auth=auth, sync_media=False)
    required = out.required

    if required in (ChangesRequired.FULL_UPLOAD, ChangesRequired.FULL_SYNC):
        full_req = pb.FullUploadOrDownloadRequest(
            auth=auth, upload=True, server_usn=out.server_media_usn
        )
        col._backend.full_upload_or_download(full_req)
    elif required == ChangesRequired.FULL_DOWNLOAD:
        full_req = pb.FullUploadOrDownloadRequest(
            auth=auth, upload=False, server_usn=out.server_media_usn
        )
        col._backend.full_upload_or_download(full_req)
    # NORMAL_SYNC / NO_CHANGES: sync_collection() already applied the delta.

    return ChangesRequired.Name(required)


def main() -> None:
    # Collection A: add one note, then sync -> seeds the server.
    a = open_col()
    model = a.models.by_name("Basic")
    note = a.new_note(model)
    note.fields[0] = "sync-probe"
    a.add_note(note, a.decks.id("Default"))
    print("A notes:", a.note_count())
    print("A sync required:", sync(a, login(a)))
    a.close()

    # Collection B: fresh, separate temp dir, sync -> must receive A's note.
    b = open_col()
    print("B notes before:", b.note_count())
    print("B sync required:", sync(b, login(b)))
    print("B notes after:", b.note_count())
    b_note_count = b.note_count()
    b.close()

    assert b_note_count >= 1, "B did not receive A's note - sync round-trip FAILED"
    print("ROUND-TRIP OK")


if __name__ == "__main__":
    main()
