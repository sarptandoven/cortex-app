# Hosting the backend — the exact steps (nothing assumed)

Follow top to bottom. Do not skip. Copy-paste the commands exactly. Every command tells you what
you should see. If you see something different, jump to "If something goes wrong" at the bottom.

You will: (1) make a key so your Mac can log into a server, (2) rent a small server, (3) point
`api.signindoppl.com` at it, (4) run ONE command that installs everything, (5) confirm it works.
Time: ~25 minutes. Cost: about **$4/month** (Hetzner CX22).

Two words you'll see:
- **Terminal** = the black-and-white typing app on your Mac. Open it: press `Cmd+Space`, type
  `Terminal`, press Enter.
- **Paste in Terminal** = `Cmd+V`, then press **Enter** to run it.

---

## PART 1 — Make an SSH key (so the server will trust your Mac). ~2 min.

An "SSH key" is a password file your Mac uses to log into servers. You make it once.

**1.1** Open Terminal (Cmd+Space → "Terminal" → Enter).

**1.2** Paste this and press Enter — it checks if you already have a key:
```bash
cat ~/.ssh/id_ed25519.pub 2>/dev/null || echo "NO KEY YET"
```
- If you see a line starting with `ssh-ed25519 ...` → you already have a key. **Skip to 1.4.**
- If you see `NO KEY YET` → continue to 1.3.

**1.3** Paste this and press Enter, then press **Enter three more times** (accept defaults, no passphrase):
```bash
ssh-keygen -t ed25519
```
You should see art like "The key's randomart image is:". Good.

**1.4** Paste this to copy your key to the clipboard:
```bash
pbcopy < ~/.ssh/id_ed25519.pub
```
Nothing prints — that's correct. Your public key is now copied. You'll paste it in Part 2.

---

## PART 2 — Rent the server (Hetzner). ~8 min.

**2.1** Go to **https://console.hetzner.com** → click **Sign up**, create an account (email +
password; they may ask for a card to prevent abuse — the CX22 is €3.79/mo).

**2.2** After login, click **+ New project**, name it `doppl`, open it.

**2.3** Click the red **Add Server** button. Set these, ignore everything else:
- **Location:** pick the one nearest your users (e.g. Ashburn/US or Nuremberg/EU).
- **Image:** **Ubuntu 24.04**.
- **Type:** click the **Shared vCPU** tab → choose **CX22** (2 vCPU / 4 GB, cheapest, fine for
  beta). (CX32 if you expect a lot of users.)
- **Networking:** leave "Public IPv4" checked.
- **SSH keys:** click **+ Add SSH key** → **paste** (Cmd+V — this is the key from step 1.4) →
  Name it `my-mac` → Add SSH key. Make sure its checkbox is ticked.
- **Volumes / Firewalls / Backups:** you can tick **Backups** (+20%, ~€0.75/mo, worth it). Leave
  the rest.
- **Name:** `doppl-api`.

**2.4** Click **Create & Buy now**.

**2.5** When it finishes (~30 sec), you'll land on the server page. **Copy the IPv4 address** shown
at the top (looks like `5.161.42.17`). Write it somewhere — you'll use it as `<SERVER-IP>` below.

---

## PART 3 — Point api.signindoppl.com at the server. ~5 min + a wait.

This is done at **wherever you bought signindoppl.com** (GoDaddy, Namecheap, Cloudflare, Porkbun,
Google Domains, etc.).

**3.1** Log into that registrar. Find **DNS settings** (a.k.a. "DNS", "Manage DNS", "DNS records")
for **signindoppl.com**.

**3.2** Add **one** record:
- **Type:** `A`
- **Name / Host:** `api`   ← just the word `api` (some registrars want `api`, a few want the full
  `api.signindoppl.com` — if unsure, `api` is right for almost all).
- **Value / Points to / IP address:** `<SERVER-IP>` (the number from step 2.5)
- **TTL:** leave default (Auto / 3600).
- **Save.**

**3.3** Wait ~5–15 minutes, then in **Terminal** paste:
```bash
dig +short api.signindoppl.com
```
- When it prints your `<SERVER-IP>` → DNS is ready. Continue.
- If it prints nothing → wait a few more minutes and run it again. (Do NOT continue until it prints
  the IP.)

---

## PART 4 — Install everything with ONE command. ~5 min.

**4.1** In Terminal, go to the project folder (paste exactly):
```bash
cd /Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei
```
It should show no error (the prompt just changes).

**4.2** Run the deploy (replace `<SERVER-IP>` with your number from 2.5):
```bash
deploy/push.sh root@<SERVER-IP> api.signindoppl.com
```
Example with a real IP: `deploy/push.sh root@5.161.42.17 api.signindoppl.com`

**4.3** The FIRST time it connects it asks:
`Are you sure you want to continue connecting (yes/no)?` → type **`yes`** and press Enter.

**4.4** It now runs for a few minutes (installs Python, the API, HTTPS certificate, firewall,
backups). Lots of text scrolls — that's normal. **Do not close Terminal.**

**4.5** At the VERY END it prints a block with secrets, including an **admin token** and a **KEK**.
**COPY BOTH into your password manager right now.** They are shown only once. The admin token is how
you'll open your user dashboard.

---

## PART 5 — Confirm it's live. ~1 min.

**5.1** In Terminal paste:
```bash
curl https://api.signindoppl.com/health
```
You should see `{"status":"ok", ...}`. ✅ The backend is live on the internet with HTTPS.

**5.2** Open a browser to **https://api.signindoppl.com/admin** → paste your **admin token** → you'll
see the user dashboard (0 users for now). ✅ You can now watch signups here anytime.

**That's it — hosting is done.** People can now create accounts in the app and their memory syncs to
this server.

---

## PART 6 — What's next (not hosting)
- Turn on Google/GitHub/Apple sign-in buttons: `docs/FOUNDER_GO_LIVE.md` Step 2 (each is a 2-minute
  console setup + two lines in the server's env file).
- Then tell me "the backend is live" and I flip the app to require accounts, rebuild the Mac App
  Store version, re-run the compliance checks, and hand you the upload command.

---

## If something goes wrong

- **`dig` prints nothing (Part 3.3):** DNS hasn't propagated. Wait 10–20 min, run it again. Confirm
  the A record's Name is `api` and Value is your exact server IP.
- **`Permission denied (publickey)` when running push.sh:** your Mac's SSH key isn't on the server.
  Re-do Part 1 (you must see `ssh-ed25519 ...`), then in Hetzner: server → **Rescue/Rebuild is NOT
  needed** — easiest fix: delete the server and redo Part 2, making sure the SSH key checkbox is
  ticked. (Or add the key to `/root/.ssh/authorized_keys` if you know how.)
- **`deploy/push.sh: No such file or directory`:** you're in the wrong folder. Re-run Part 4.1 (the
  `cd` command), then try again.
- **`curl` in Part 5 hangs or errors:** wait 2 min (the HTTPS certificate can take a moment on first
  boot), then retry. If it still fails, the deploy may have hit an error — copy the last ~20 lines
  Terminal printed and send them to me.
- **Anything else:** copy the exact command you ran and the exact output, send it to me, and I'll
  tell you the precise next move.

---

## Later: update the server after a new app/backend version
```bash
cd /Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei
deploy/push.sh root@<SERVER-IP> --update
```

## Later: see your users
Open **https://api.signindoppl.com/admin**, paste your admin token. Total/active/pending users,
signups per day, which provider they used, searchable list.
