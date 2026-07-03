# Sync server for GMAT-on-Anki, deployed to fly.io.
#
# IMPORTANT: this builds `anki-sync-server` from YOUR FORK (not upstream Anki),
# so the sync protocol version matches your desktop app + the iOS bridge (both
# built from the same fork's rslib). A version mismatch is the #1 cause of
# "sync failed" — building from the fork avoids it.
#
# Push your fork first so fly can fetch it:
#   git -C anki push origin main
# Then override FORK_REF below if you deploy from a different branch.

FROM rust:1.92.0-alpine3.23 AS builder
ARG FORK_GIT=https://github.com/graceyan212/anki.git
ARG FORK_REF=main
RUN apk add --no-cache build-base protobuf
# Builds just the sync-server binary from the fork (pulls in rslib).
RUN cargo install --git ${FORK_GIT} --branch ${FORK_REF} --locked \
    --root /server anki-sync-server

FROM alpine:3.23
RUN apk add --no-cache libgcc ca-certificates
COPY --from=builder /server/bin/anki-sync-server /usr/local/bin/anki-sync-server

# The server reads these from the environment. SYNC_USER1 is set as a fly SECRET
# (not baked into the image) — see README. Data lives on a persistent volume.
ENV SYNC_HOST=0.0.0.0 \
    SYNC_PORT=8080 \
    SYNC_BASE=/anki_data

EXPOSE 8080
VOLUME /anki_data
CMD ["anki-sync-server"]
