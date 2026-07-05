# Cortex KEK — custody, escrow, and the restore drill

For the **operator**. The KEK is the single most important secret Cortex holds. Read
this before public launch; the restore drill below is a launch gate.

## What the KEK is and what it protects

- The **KEK** (Key Encryption Key) lives at `/etc/cortex/kek` on the server (env
  `CORTEX_KEK_FILE`). It is a 32-byte master key held by the `LocalKekProvider`.
- It does **not** encrypt user data directly. It **wraps each user's per-user data key
  (DEK)**. Those per-user DEKs are what actually encrypt:
  - **connector credentials** — the OAuth tokens/secrets Cortex uses to read connected
    sources (Phase 1, live today); and, as later phases ship, hosted vault files,
    backups, and content columns (see `docs/ACCOUNTS_ENCRYPTION_DESIGN.md` §4).
- It powers **crypto-erase-on-delete**: deleting an account destroys that user's wrapped
  DEK, which permanently and instantly renders their encrypted data — including in every
  backup — unreadable. That is what makes "delete my account" cryptographically final.

## The golden rule

> **Lose the KEK → all encrypted user data becomes permanently unrecoverable.**
> **Steal the KEK *and* a data backup → that data can be read.**

So the KEK must be (a) **never lost**, and (b) **stored separately from the data
backups**. Both halves matter. A KEK alone (without a backup) exposes nothing; a backup
alone (without the KEK) is unreadable ciphertext — which is exactly the design.

## Escrow procedure (do this right after deploy)

`bootstrap.sh` prints the KEK on first deploy; it is also at `/etc/cortex/kek`. Store
**two copies**, in two independent places:

1. **Password manager** (1Password / Bitwarden), labeled clearly, e.g.
   `Cortex KEK — production`.
2. **An offline location you control** that is **NOT co-located with the data backups** —
   an encrypted USB drive, or a sealed printout in a safe. If your backups go to a cloud
   bucket, the offline copy must not be in that same cloud/account.

Also escrow, in the same password manager (they are not the KEK, but you need them):
the admin token (`CORTEX_API_KEY`, prefix `cxop_`) and server access credentials.

**Permissions:** the KEK file is `root:cortex`, mode `0440`. Do not loosen it. Never
print it into logs, tickets, chat, or a screen share.

## Why the nightly backup excludes the KEK (by design)

`deploy/backup.sh` deliberately does **not** include `/etc/cortex/kek`. This is the
whole point of at-rest encryption: if a backup archive is stolen, it is unreadable
ciphertext without the KEK. If you ever "helpfully" add the KEK to the backup, you undo
the protection — a single stolen archive would then expose every user's data. Keep the
KEK out of the backups, and keep the escrowed KEK out of wherever the backups live.

- The backup snapshots the SQLite DBs (WAL-consistent `.backup`), the vault trees
  (credential blobs are CXE1-encrypted at rest), and the env file **without** the KEK.
- Verify occasionally that no one added the KEK: it should not appear inside any archive
  under `/var/lib/cortex/backups`.

## Pre-launch restore drill (LAUNCH GATE)

Never tell users their data is "encrypted" until you have proven you can restore it with
your **escrowed** KEK. Claiming encryption you cannot restore is worse than not claiming
it. Do this drill, and re-do it at least every 30 days.

1. **Spin up a scratch box** (a throwaway VM), separate from production.
2. **Copy the latest backup archive** from `/var/lib/cortex/backups` onto it and unpack
   it into a scratch data root. Do **not** copy production's KEK off the box in the
   normal course — use the **escrowed** copy in step 4, which is what proves escrow works.
3. **Restore the SQLite DBs and vault trees** into the scratch `/var/lib/cortex` layout.
4. **Supply the escrowed KEK** (from your password manager or offline copy — not by
   scraping production) into `/etc/cortex/kek` on the scratch box, with `0440` perms.
5. **Confirm a credential decrypts:** start the app against the restored data and verify
   a stored connector credential reads back correctly (e.g. a connector loads / a sync
   authenticates), proving the escrowed KEK unwraps a real user's DEK.
6. **Tear down the scratch box.** Record the date — this satisfies the go/no-go checklist
   item ("restore drill done in the last 30 days; credential decrypted with the escrowed
   KEK"). If the credential does **not** decrypt, you have the wrong KEK escrowed —
   fix it before launch.

## Rotation (future / advanced)

KEK rotation is designed but operationally advanced — treat it as a planned maintenance
task, not a live incident action.

- **How it works:** rotation re-wraps the per-user key wraps (`user_key_wraps`) under a
  new KEK version. Each row carries its own `kek_id`, so mixed states are valid mid-flight
  and the pass is incremental and resumable. It touches **no user-data bytes** — only the
  wrapped DEKs — so it is fast even at scale. Do NOT rely on any provider's auto-rotation;
  that only versions future wraps and never re-wraps existing DEKs. Cadence ~yearly per
  NIST SP 800-57. See `docs/ACCOUNTS_ENCRYPTION_DESIGN.md` §4 "Rotation".
- **Suspected compromise:** if the KEK may have leaked, contain access first, then plan a
  rotation on a maintenance window with a fresh backup and the current escrowed KEK on
  hand. See `HOSTED_INCIDENTS.md` §8.
- **Migration path (LocalKek → cloud KMS):** the KEK sits behind a `KeyProvider`
  interface with two implementations — `LocalKekProvider` (the file KEK you escrow today)
  and `KmsKekProvider` (AWS/GCP KMS, binding unwraps to each tenant via encryption
  context). Moving to KMS before ~100k users is a config swap plus a one-time incremental
  re-wrap job; the file-KEK stays a permanently supported self-hosting option. Details in
  `docs/ACCOUNTS_ENCRYPTION_DESIGN.md` §5 (the 10k → 1M swap table).
