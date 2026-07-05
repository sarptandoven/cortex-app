# HERMES TASK — Notarize the Cortex DMG and ship it to the site

You are **Hermes**, operating on the founder's Mac. Goal: turn the currently
**unsigned (ad-hoc)** Cortex DMG — which Gatekeeper rejects — into a **Developer-ID
signed, notarized, stapled** DMG that opens on a normal double-click, then publish it
to the website. When you finish, `https://trydoppl.com/downloads/Cortex-*.dmg` is a
build a stranger can download and open with no warnings and no right-click.

The build/sign/notarize pipeline already exists and is correct. Your job is
**credential setup (one human step by the founder) + running it + verifying + shipping.**
Read `docs/runbooks/NOTARIZE_DMG.md` alongside this — it has the credential detail; this
file is the ordered operator task with guardrails and acceptance.

────────────────────────────────────────────────────────────────────────────
0. GUARDRAILS (read first)
────────────────────────────────────────────────────────────────────────────
- **Never commit or print secrets** — the `.p8` API key, app-specific password, or the
  cert private key. They live in the login keychain / a secure path, never in git.
- **The certificate is the founder's to create** (it needs their Apple ID + a browser
  login). Do NOT try to buy, enroll, or generate Apple credentials autonomously — if the
  cert or notary profile is missing, STOP and hand the founder §1 of NOTARIZE_DMG.md.
- **Notarization uploads the app to Apple.** That is expected and fine (it is how
  notarization works) — it is not "publishing to the world." Publishing the site (§5) is
  the outward step; do it, since shipping the download is the point of this task.
- **Git discipline:** work on `mass-scale-app-redesign` (or `main` if merged). Push to
  both remotes (origin = sarptandoven/cortex-by-doppl, doppl = doppl-tech/cortex-app).
  Never force-push.
- **Verify every phase.** A phase is not done until its check passes. If notarization
  returns "Invalid", read the log (§Troubleshooting) and fix before continuing.

────────────────────────────────────────────────────────────────────────────
1. FOUNDER PROVIDES (one-time; ask if missing) — then everything else is you
────────────────────────────────────────────────────────────────────────────
Two values, both set up per NOTARIZE_DMG.md §1–§2:
- **Developer ID Application identity** — the full string from
  `security find-identity -v -p codesigning | grep "Developer ID Application"`
  e.g. `Developer ID Application: Doppl Inc (AB12CD34EF)`.  → env `CORTEX_CODESIGN_IDENTITY`
- **notarytool keychain profile name** — created with `xcrun notarytool store-credentials`
  e.g. `cortex-notary`.  → env `CORTEX_NOTARY_PROFILE`

Confirm both exist before starting:
```bash
security find-identity -v -p codesigning | grep "Developer ID Application" || echo "NO CERT — stop, ask founder (NOTARIZE_DMG.md §1)"
xcrun notarytool history --keychain-profile "cortex-notary" >/dev/null 2>&1 && echo "notary profile OK" || echo "NO NOTARY PROFILE — stop, ask founder (NOTARIZE_DMG.md §2)"
```
If either prints "NO …", STOP and give the founder the matching NOTARIZE_DMG.md section.
Everything below is yours to run.

────────────────────────────────────────────────────────────────────────────
2. BUILD + SIGN + NOTARIZE + STAPLE (one command)
────────────────────────────────────────────────────────────────────────────
```bash
cd macos
CORTEX_CODESIGN_IDENTITY="Developer ID Application: <NAME> (<TEAMID>)" \
CORTEX_NOTARY_PROFILE="cortex-notary" \
./package_release.sh --production
```
`--production` makes it FAIL LOUDLY if the identity/profile/HTTPS/bundled-Python aren't
all present, so it can never silently ship an unsigned build. Under the hood it bundles
Python + the vector runtime + the model2vec model, hardened-runtime-signs every nested
Mach-O and the .app with DeveloperID.entitlements, signs the DMG, runs
`notarytool submit --wait` (blocks ~1–5 min until Apple returns Accepted), then
`stapler staple` + `stapler validate` + an `spctl` gate. Artifacts land in
`outputs/Cortex-<version>/`.

CHECK: the command exits 0 and the log shows `notarytool` status **Accepted** and
`stapler` **validated**. If it exits non-zero, go to Troubleshooting; do not proceed.

────────────────────────────────────────────────────────────────────────────
3. VERIFY THE NOTARIZED BUILD (must all pass)
────────────────────────────────────────────────────────────────────────────
```bash
DMG="$(ls -t ../outputs/Cortex-*/Cortex-*.dmg | head -1)"
MP="$(hdiutil attach "$DMG" -nobrowse -readonly | awk -F'\t' '/\/Volumes\// {print $NF}' | tail -1)"
APP="$MP/Cortex.app"

# a) Signed + hardened + notarized (not ad-hoc):
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Authority|TeamIdentifier|flags"   # Authority=Developer ID Application…, TeamIdentifier set, flags=…(runtime)
spctl -a -vvv --type execute "$APP"                                                 # => accepted, source=Notarized Developer ID
xcrun stapler validate "$DMG"                                                        # => validated

# b) Semantic search is actually bundled (the real product value):
python3 ../scripts/check_vector_runtime.py --app "$APP"                              # => "status":"ok", vector available:true
ls "$APP/Contents/Resources/model2vec/config.json"                                   # present (bundled model)

hdiutil detach "$MP" -quiet
```
Both (a) and (b) must pass. (a) means it opens with a double-click; (b) means retrieval
uses the real model, not the hash fallback. If (a) shows `adhoc`/`rejected`, the identity
wasn't picked up (keychain locked or wrong identity string) — fix and rebuild.

Real-world confirmation: download the DMG on a **second** Mac (or a fresh user account)
and double-click — it should open with no warning, no right-click.

────────────────────────────────────────────────────────────────────────────
4. PUBLISH TO THE SITE
────────────────────────────────────────────────────────────────────────────
Copy the notarized artifacts into the served dir and regenerate the manifests **from the
actual files** (so checksums match the notarized bytes):
```bash
cd ..                                   # repo root
SRC="outputs/Cortex-0.1.0-1"; DST="site/downloads"   # adjust version if bumped
cp "$SRC"/Cortex-*.dmg "$SRC"/Cortex-*.app.zip "$DST/"
( cd "$DST" && shasum -a256 Cortex-*.dmg Cortex-*.app.zip > "$(ls Cortex-*.checksums.txt | head -1)" )
python3 - <<'PY'
import json, hashlib, os
dst="site/downloads"
def sha(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for c in iter(lambda: f.read(1<<20), b""): h.update(c)
    return h.hexdigest()
for man in ("distribution.json","latest.json"):
    d=json.load(open(f"{dst}/{man}"))
    for a in d.get("artifacts",[]):
        p=os.path.join(dst,a["filename"])
        if os.path.exists(p): a["sha256"]=sha(p); a["size_bytes"]=os.path.getsize(p)
    d["artifacts"]=[a for a in d.get("artifacts",[]) if os.path.exists(os.path.join(dst,a["filename"]))]
    json.dump(d, open(f"{dst}/{man}","w"), indent=2); open(f"{dst}/{man}","a").write("\n")
print("manifests regenerated from notarized files")
PY
python3 scripts/check_distribution_site.py                         # => "status":"ok"
( cd site/downloads && shasum -a256 -c Cortex-*.checksums.txt )     # => OK
```
Also drop the "right-click → Open" caveat from the site copy where it appears
(`site/index.html`, `site/downloads/latest.json` install_steps / known_limitations) — a
notarized build no longer needs it.

────────────────────────────────────────────────────────────────────────────
5. COMMIT + PUSH + DEPLOY
────────────────────────────────────────────────────────────────────────────
```bash
git add site/downloads/Cortex-*.dmg site/downloads/Cortex-*.app.zip \
        site/downloads/Cortex-*.checksums.txt site/downloads/distribution.json \
        site/downloads/latest.json site/index.html
git commit -m "Ship notarized Cortex DMG (Developer ID; Gatekeeper double-click clean)"
# push to BOTH remotes (use the founder's gh token helper; never print it):
TOKEN=$(env -u GITHUB_TOKEN gh auth token)
for url in "https://x-access-token:${TOKEN}@github.com/sarptandoven/cortex-by-doppl.git" \
           "https://x-access-token:${TOKEN}@github.com/doppl-tech/cortex-app.git"; do
  git push "$url" mass-scale-app-redesign 2>&1 | sed "s/${TOKEN}/<redacted>/g"
done
```
Then deploy the site however trydoppl.com is served (static host / Pages / bucket sync).
CHECK: `curl -sI https://trydoppl.com/downloads/$(basename "$SRC"/Cortex-*.dmg)` → 200,
and `shasum -a256` of the downloaded file matches `latest.json`.

NOTE (repo hygiene): the DMG + app.zip are committed as raw binaries (~140MB/release).
Before the next release, consider moving downloads to **Git LFS** or **GitHub Releases**
so history stops growing. Flag this to the founder; don't change hosting unasked.

────────────────────────────────────────────────────────────────────────────
6. ACCEPTANCE — done when ALL are true
────────────────────────────────────────────────────────────────────────────
[ ] `package_release.sh --production` exited 0; notarytool = Accepted; stapler = validated.
[ ] `spctl -a` on the app = "accepted, source=Notarized Developer ID" (not rejected/adhoc).
[ ] `check_vector_runtime.py` = ok and `Resources/model2vec/config.json` present.
[ ] Fresh download opens with a plain double-click on a second Mac — no warning.
[ ] `site/downloads` swapped; `check_distribution_site.py` ok; checksums verify.
[ ] Committed + pushed to both remotes; site deployed; live URL serves the notarized DMG.
[ ] Right-click-Open caveat removed from the site copy.
Report this checklist's status to the founder.

────────────────────────────────────────────────────────────────────────────
7. TROUBLESHOOTING
────────────────────────────────────────────────────────────────────────────
- notarytool status **Invalid** → `xcrun notarytool log <submission-id> --keychain-profile cortex-notary`
  lists the exact offending binary (almost always an unsigned nested wheel `.so`). A clean
  `package_release.sh --production` re-signs every Mach-O, so a fresh rebuild usually fixes it.
- `codesign` can't find the identity → the login keychain must be unlocked in this session:
  `security unlock-keychain`. Re-check with `security find-identity -v -p codesigning`.
- `spctl` still rejects after stapling → confirm `flags=…(runtime)` in `codesign -dv`; if
  absent, the hardened runtime wasn't applied (identity was `-`); the build wasn't `--production`.
- notarytool hangs → it waits on Apple; typical 1–5 min, can be longer under load. Do not
  Ctrl-C; if it truly stalls, re-run `notarytool submit` (idempotent).
