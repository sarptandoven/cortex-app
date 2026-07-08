# Doppl — Finish-Line Guide (Google + Apple + App Store)

_Written for your exact setup: app **Doppl**, bundle **com.cortex.doppl**, Apple Team **P4H96J3VXY**._
_Researched + verified against the current (2026) Google Cloud and Apple Developer consoles._

## The whole picture — what's left and who does it

| # | Item | Where | Who | Result you send me |
|---|------|-------|-----|--------------------|
| 1 | Google OAuth credentials | console.cloud.google.com | You (~15 min) | Client ID + Secret |
| 2 | Enable **Sign in with Apple** on the App ID | developer.apple.com | You (~5 min) | (nothing — just do it) |
| 3 | **Mac App Store provisioning profile** | developer.apple.com | You (~5 min) | the `.provisionprofile` file |
| 4 | Signing certificates | your Mac | ✅ **ALREADY DONE** | — |
| 5 | Build the signed `.pkg` | your Mac | **Me** (once I have #3) | — |
| 6 | App Store Connect: app record, privacy, upload, submit | appstoreconnect.apple.com | You (I'll guide) | — |

**Do #1, #2, #3 in any order. Send me the Client ID + Secret and the `.provisionprofile`, and I take it from there.**

> **Good news on certificates (#4):** I checked your Mac directly — you **already have BOTH** signing certificates you need:
> - `3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)` (signs the app)
> - `3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)` (signs the installer `.pkg`)
>
> So you can **skip creating any certificate.** (If you ever set this up on a *different* Mac, the full from-scratch cert steps are in the Appendix.)

---

# PART 1 — Google: get the "Continue with Google" credentials

## What you're doing and why (read this first)

We are adding a **"Continue with Google"** button to Doppl. For that to work, Google needs to know who we are and where to send users back after they log in. You will create two things inside Google's website:

1. A **consent screen** — the little "Doppl wants to access your Google Account" page users see.
2. An **OAuth Client ID + Client Secret** — a username/password pair that our backend uses to talk to Google.

At the very end you will copy **two values** (a Client ID and a Client Secret) and send them to me. That's the whole goal. Nothing you do here touches the server — I plug those two values into `/etc/cortex/cortex.env` and restart the service myself.

**Good news up front:** we only ask for a name, email address, and profile picture. Google calls these **"non-sensitive" scopes**, which means **no Google review, no verification, no "security assessment", and no waiting.** You can publish immediately. (Confirmed by Google's own docs — see Sources.)

**Time needed:** about 15 minutes. **You'll need:** a Google account (a personal Gmail is fine).

---

## PART 0 — Sign in

1. Open a web browser (Chrome is easiest) and go to **https://console.cloud.google.com**.
2. If asked, **sign in** with the Google account you want to own this project. A personal Gmail is completely fine.
   - **You should SEE:** the Google Cloud dashboard, with a blue bar across the top. In the top-left is a **"☰" menu icon** (three horizontal lines) and the words **"Google Cloud"**.
3. **First time only:** if a "Welcome" / "Try for free" / "Agree and continue" box appears, pick your **Country**, tick the **Terms of Service** checkbox, and click **AGREE AND CONTINUE**. You do **NOT** need to enter a credit card or start a free trial — click **Skip / Not now** if it asks. OAuth login is free.

---

## PART 1 — Create (or select) a Google Cloud project

Everything in Google Cloud lives inside a "project." We'll make one just for Doppl.

4. At the very top of the page, next to the words **"Google Cloud"**, there is a **project picker** — a button that either says **"Select a project"** or shows the name of a project. **Click it.**
   - **You should SEE:** a pop-up window titled **"Select a project"** (or **"Select from …"**).
5. In that pop-up, click **NEW PROJECT** (top-right of the pop-up).
   - **You should SEE:** a page titled **"New Project"**.
6. In the **Project name** box, type: **Doppl**
   - Leave **"Organization"** and **"Location"** as whatever they default to (likely **"No organization"**). You don't need to change these.
   - (Google auto-generates a "Project ID" like `doppl-123456` — that's fine, ignore it.)
7. Click the blue **CREATE** button.
   - **You should SEE:** a spinning notification in the top-right for a few seconds, then **"Creating project Doppl… done."**
8. **CRITICAL — this silently trips people up:** After it finishes, the top bar might **still show a different/old project**. Click the **project picker** at the top again, and in the pop-up **click "Doppl"** to switch into it.
   - **You should SEE:** the top bar now reads **"Google Cloud  ▸  Doppl"**. Everything you do from now on must happen while **"Doppl" is the selected project** — if you ever do the wrong thing in the wrong project, it just won't work and Google won't warn you. Glance at the top bar occasionally to confirm it says **Doppl**.

---

## PART 2 — Configure the consent screen (the "Google Auth Platform")

**Important note about the current UI:** Google renamed and reorganized this whole area. The old menu item **"APIs & Services → OAuth consent screen"** is **gone**; it's now called **"Google Auth platform"** with tabs named **Branding**, **Audience**, **Data Access**, and **Clients**. I'll give you the new path first and the old path as a fallback.

9. **NEWEST PATH (use this):** Click the **☰ menu icon** (three lines, top-left) → hover/click **"Google Auth platform"** → click **"Branding"**.
   - **Shortcut alternative:** just paste this into your address bar: **https://console.cloud.google.com/auth/branding** (make sure the project shown is **Doppl**).
   - **OLDER FALLBACK (if you don't see "Google Auth platform"):** ☰ menu → **"APIs & Services"** → **"OAuth consent screen"**. Google will redirect you into the same new screens.
10. **You should SEE one of two things:**
    - **(a)** A page with a blue **"GET STARTED"** button and text like *"Google Auth Platform not configured yet."* → **Click "GET STARTED"** and continue to step 11.
    - **(b)** If it's somehow already partly set up, you'll land directly on the Branding form → skip to step 12.

11. After clicking **GET STARTED**, a short wizard appears. Fill it in **exactly** like this:
    - **"App Information" step:**
      - **App name:** type **Doppl**
      - **User support email:** click the dropdown and choose **your own email address** (this is shown to users who need help). Click **NEXT**.
    - **"Audience" step:** choose **External**.
      - *(Why: "Internal" only exists/works if you pay for a Google Workspace organization and only lets your own company's employees log in. We want any Google user to be able to sign into Doppl, so it must be **External**.)* Click **NEXT**.
    - **"Contact Information" step:**
      - **Email addresses:** type **your own email address** again (Google uses this to email you about the project). Click **NEXT**.
    - **"Finish" step:**
      - Tick the checkbox **"I agree to the Google API Services: User Data Policy."**
      - Click **CONTINUE**, then click **CREATE**.
   - **You should SEE:** it drops you onto an **"Overview"** or **"Branding"** page for the Google Auth Platform, now showing **App name: Doppl**.

12. **Double-check the Branding values** (click the **"Branding"** tab on the left if you're not already there):
    - **App name** = **Doppl**
    - **User support email** = your email
    - **Developer contact information / Email addresses** = your email
    - You do **NOT** need a logo, a homepage URL, a privacy policy URL, or an "authorized domain" for non-sensitive login. Leave those blank. (If any of these are blank later warnings appear, they only matter for *sensitive* scopes, which we're not using.)
    - If you edited anything, click **SAVE**.

---

## PART 3 — Add the scopes (name / email / profile) and confirm they're NON-SENSITIVE

"Scopes" = exactly what Doppl is allowed to see. We only want three, and all three are the harmless basic-identity ones.

13. On the left, click the **"Data Access"** tab.
    - **OLDER FALLBACK:** in the old UI this was a step called **"Scopes"** inside the OAuth consent screen wizard.
    - **Shortcut alternative:** **https://console.cloud.google.com/auth/scopes** (project = Doppl).
14. Click the button **"ADD OR REMOVE SCOPES"**.
    - **You should SEE:** a panel slide in from the right with a long, searchable table of scopes and checkboxes.
15. Add these **three** scopes. The easiest reliable way: in the **"Manually add scopes"** box near the bottom of that panel, paste the following three lines (one per line, or comma-separated), then click **"ADD TO TABLE"**:
    ```
    openid
    https://www.googleapis.com/auth/userinfo.email
    https://www.googleapis.com/auth/userinfo.profile
    ```
    - **Alternative (via the table):** use the **filter/search box** at the top of the table and search for `userinfo.email`, tick its checkbox; then `userinfo.profile`, tick it; then `openid`, tick it. Some UIs show these with the friendly labels **"See your primary Google Account email address"**, **"See your personal info, including any personal info you've made publicly available"**, and **"Associate you with your personal info on Google."**
16. Confirm all three now appear in the list, and click **"UPDATE"** (in the slide-in panel), then **"SAVE"** on the Data Access page.
17. **Confirm they are NON-SENSITIVE (this is the part that means "no Google review"):**
    - On the Data Access page the scopes will be grouped into sections. Your three must all sit under the section titled **"Your non-sensitive scopes"**.
    - The sections **"Your sensitive scopes"** and **"Your restricted scopes"** must be **EMPTY**.
    - **Why this matters:** because you only have non-sensitive scopes, Google requires **NO verification, NO security assessment, NO CASA audit, and NO waiting period.** If you ever accidentally add a Gmail/Drive/Calendar scope, it would jump into "sensitive/restricted" and trigger a review — so keep it to just these three.

---

## PART 4 — Testing vs. Production, and publishing (no verification needed)

This controls **who is allowed to click "Continue with Google"** in Doppl.

18. On the left, click the **"Audience"** tab.
    - **Shortcut alternative:** **https://console.cloud.google.com/auth/audience** (project = Doppl).
19. Near the top you'll see **"Publishing status"**. It currently says **"Testing"**. Here's what each mode means:
    - **Testing (the current, default state):**
      - Only Google accounts you **explicitly add as "Test users"** can sign in. Everyone else is blocked with an "app not verified / access denied" error.
      - Test users see an extra **"Google hasn't verified this app"** warning screen.
      - **Logins expire after 7 days**, forcing re-login. Fine for you and me to test, bad for real users.
      - There's a **cap of 100 test users**.
    - **In production (what you want for launch):**
      - **Any** Google account holder can sign into Doppl. No test-user list.
      - **Because our scopes are only non-sensitive**, users see **NO unverified-app warning**, and their logins **do NOT expire after 7 days**.
20. **(Optional — only if you want to test before launching):** Under **"Test users"** click **"ADD USERS"**, type your own email + mine, and **SAVE**. This lets those specific accounts try the Google button while still in Testing mode.
21. **To go live:** on the **Audience** tab, click the button **"PUBLISH APP"** (it's next to "Publishing status: Testing").
    - A confirmation dialog titled **"Push to production?"** appears. Click **"CONFIRM"**.
    - **You should SEE:** Publishing status now reads **"In production."**
    - **CRITICAL CONFIRMATION:** because we only use the three non-sensitive scopes, **NO "Prepare for verification" prompt should force you to submit anything, and no verification is required.** If Google *does* pop up any request for a homepage, a demo video, a privacy-policy URL, or an "app verification" — **stop and tell me**, because that only happens if a sensitive scope sneaked in (go back to Part 3 and remove it).
    - *(You can publish now, or stay in Testing while we build — either is fine. Publishing is not required to generate the Client ID in Part 5; the login just won't work for the public until you publish.)*

---

## PART 5 — Create the OAuth Client ID (Web application) with our redirect URI

This is the step that actually produces the Client ID + Secret I need.

22. On the left, click the **"Clients"** tab.
    - **NEWEST PATH shortcut:** **https://console.cloud.google.com/auth/clients** (project = Doppl).
    - **OLDER FALLBACK:** ☰ menu → **"APIs & Services"** → **"Credentials"** → then **"+ CREATE CREDENTIALS"** → **"OAuth client ID"**. This lands in the same place.
23. Click the button **"+ CREATE CLIENT"** (older UI: **"CREATE CREDENTIALS" → "OAuth client ID"**).
24. **Application type:** click the dropdown and choose **"Web application"**.
    - **CRITICAL:** it must be **"Web application"** — **not** "Desktop app", "iOS", "Android", or "TVs and Limited Input devices." Our backend is a web server. Picking the wrong type is a common silent mistake that makes login fail later with a `redirect_uri_mismatch` error.
25. **Name:** type something recognizable, e.g. **Doppl backend (web)**. (This name is only for your eyes inside the console; users never see it.)
26. Find the section **"Authorized JavaScript origins"** — **leave it EMPTY**. We don't need it (our login is handled server-side, not in the browser).
27. Find the section **"Authorized redirect URIs"**. Click **"+ ADD URI"**, and in the box paste **this EXACT value** (copy-paste it, do not retype — a single wrong character silently breaks login):
    ```
    https://api.signindoppl.com/v1/auth/oauth/google/callback
    ```
    - **CRITICAL — must match to the character:** it must be **https** (not http), **no trailing slash**, **no spaces**, all lowercase, exactly `api.signindoppl.com`. If this doesn't match what our server sends, Google shows users **"Error 400: redirect_uri_mismatch"** and login dies. This is the single most important field on the page.
28. Click the blue **"CREATE"** button at the bottom.

---

## PART 6 — Copy the Client ID + Client Secret (and how to re-find them)

29. **You should SEE:** a pop-up titled **"OAuth client created"**. It shows:
    - **Your Client ID** — a long string ending in **`.apps.googleusercontent.com`**.
    - **Your Client Secret** — a shorter string, often starting with **`GOCSPX-`**.
    - There is usually a small **copy icon** (📋) next to each, and a **"⬇ DOWNLOAD JSON"** button.
30. **Copy BOTH now.** Click the copy icon next to **Client ID**, paste it somewhere safe (a note/email draft). Do the same for **Client Secret**. Or click **DOWNLOAD JSON** to save a file containing both — that file is the safest way to send me everything at once.
    - **CRITICAL — the full secret is shown ONLY this one time:** Once you close this pop-up, Google will **never show the full Client Secret again** — later it only displays the **last 4 characters**. So don't close the box until you've copied/downloaded it. (If you lose it, it's not the end of the world — see step 32.)
31. Click **"OK"** to close the pop-up.

### How to re-find these later
32. Any time later: **Clients** tab → click the **name** of the client (**"Doppl backend (web)"**).
    - The **Client ID** is shown in full and can be copied again anytime.
    - The **Client Secret** is **partially hidden** (last 4 chars only). If you didn't save it and need it again, click **"ADD SECRET"** / **"ROTATE SECRET"** to generate a brand-new secret (the old one keeps working until you delete it). Send me the new one if you do this.
    - This same page is also where you can **edit the redirect URI** later if we ever change domains.

---

## PART 7 — Send me the two values (this is the finish line)

33. Send me these **two values** (paste them in our chat, or just send me the **downloaded JSON file** from step 30):
    - **Client ID** (ends in `.apps.googleusercontent.com`)
    - **Client Secret** (usually starts with `GOCSPX-`)
34. That's everything on your side. I will put them into the backend's `/etc/cortex/cortex.env` and restart the service — **you do NOT touch the server.** Once that's done, **"Continue with Google"** will work in Doppl.

**Treat the Client Secret like a password** — don't post it publicly (not in a GitHub issue, screenshot, or public channel). Our chat is fine.
---

# PART 2 — Apple: enable Sign in with Apple + make the provisioning profile

_You do NOT need to create any certificate (you already have both — see the top). You only need to (A) turn on Sign in with Apple for the App ID, and (B) generate + download the provisioning profile, then send me that file._

These steps are written for the Apple Developer website as it looks in mid-2026. Everything happens in a web browser at **developer.apple.com** — you do NOT need a Mac terminal for any of this. You only need the `.provisionprofile` file at the very end, which you'll hand to your build.

> **Who can do this:** You must be signed in as the **Account Holder** or an **Admin** of the Apple Developer account for Team ID **P4H96J3VXY** (Apple's official docs list "Account Holder or Admin" as the required role for every step below). If you're the founder and it's your own account, you're the Account Holder — you're fine. **The one thing that silently blocks you:** if a button below (Edit, Configure, the "+" button, Generate) is greyed out or missing, it almost always means you're signed in with a role that lacks permission. Nothing will error — the button just won't be there.

---

## PART 0 — Sign in and open the right place

1. Open a web browser and go to **https://developer.apple.com/account** .
2. Click **Sign In** (top right) if you're not already signed in. Sign in with the Apple ID that owns the Doppl developer account (Team ID **P4H96J3VXY**).
   - **You should SEE:** the account overview with your name/team. If there's a team dropdown near the top-left, make sure the selected team is the one for **P4H96J3VXY** (it may read "Sarp Doven" or your company/individual name). If you're a member of more than one team and the wrong one is selected, every identifier and profile below will be missing — this is a common silent trap.
3. On the overview page, click **Certificates, Identifiers & Profiles** (it appears as a tile on the overview and/or as a link in the left sidebar).
   - **You should SEE:** a new page whose left sidebar lists: **Certificates**, **Identifiers**, **Devices**, **Profiles**, **Keys**, **Services**, **More** (the exact set can vary slightly). Everything below lives in this section.

---

## PART A — Enable "Sign in with Apple" on the App ID com.cortex.doppl

4. In the left sidebar, click **Identifiers**.
   - **You should SEE:** a table titled "Identifiers" listing your App IDs. There's a search box on the right and a blue **"+"** (Register a new identifier) button on the left.
5. Find the row whose **IDENTIFIER** column reads **com.cortex.doppl** (the **NAME** column will show whatever description you gave it, e.g. "Doppl"). If you have many, type `com.cortex.doppl` into the search box (top right) to filter.
   - **If com.cortex.doppl is NOT in the list:** stop — it means the App ID was never registered, and Part B can't work either. (Registering it is a separate task: the "+" button → "App IDs" → "App" type, with that exact bundle ID `com.cortex.doppl`.) For this task we assume it already exists.
6. Click the **com.cortex.doppl** row (click the name/identifier text).
   - **You should SEE:** the App ID detail page, headed **"Edit your App ID Configuration"** or similar, showing the **Description** (name) and the **Bundle ID** `com.cortex.doppl`.
7. **Click the "Edit" button** (Apple's current official instructions read: *"Select the App ID you want to update, then click Edit."*). Depending on the exact console build you may land directly on an editable page, in which case there is nothing to click — but if you see a read-only view with an **Edit** button (usually top-right), click it now.
   - **You should SEE:** the page becomes editable. Below the Description field there are **two tabs**: **Capabilities** and **App Services**. Make sure you are on the **Capabilities** tab (this is where "Sign in with Apple" lives — it is NOT under "App Services").
   - **Why this matters:** "Sign in with Apple" issues a real entitlement into your provisioning profile, so it is a *Capability*, not an *App Service*. If you're on the wrong tab you simply won't find it and may think it's missing.
8. On the **Capabilities** tab, scroll the list until you find the row **Sign in with Apple** (icon is a small Apple logo). **Check the checkbox** on the left of that row.
   - **You should SEE:** the row become active/highlighted, and a **Configure** button appear on the right side of that same row. (If no "Configure" button appears, you likely aren't in edit mode — go back to step 7 and click **Edit** first.)
9. Click **Configure** on the "Sign in with Apple" row.
   - **You should SEE:** a dialog/panel for the Sign in with Apple App ID configuration. It asks you to choose whether this App ID is **enabled as a primary App ID** or **grouped with an existing primary App ID**, and it has an optional field for a **server-to-server notification endpoint URL**.
10. Choose **"Enable as a primary App ID"**. This is correct for Doppl because it is a standalone app and is not sharing one Apple-login identity with a different, already-existing app of yours.
    - **Note:** the server-to-server endpoint field only accepts a value when the App ID is set to *primary* — another reason "primary" is the right choice here.
11. Leave the **server-to-server notification endpoint** field **BLANK**. You do not need it, and a wrong/incomplete URL here can cause problems. (You'd only fill this in later if you implement account-revocation webhooks; per Apple it must be a full `https://…` URL using TLS 1.2.)
12. Click **Save** inside that dialog/panel (the button is labeled **Save** — Apple's docs: *"Click Save to record your configuration selection."*).
    - **You should SEE:** the dialog close, and back on the Capabilities tab the "Sign in with Apple" checkbox stays checked.
13. **Click the "Save" button at the TOP-RIGHT of the whole App ID page** to commit the change onto the App ID (Apple's docs: *"Click Save on the top right to record the changes to your App ID."*).
    - **IMPORTANT — there are TWO Saves:** the first Save (step 12) only records the Sign-in-with-Apple sub-configuration inside the dialog; this second Save (top-right of the page) is what actually writes the capability onto the App ID. **If you skip this top-right Save, nothing is saved and this whole part silently did nothing.**
14. If a warning dialog appears (Apple's docs: *"If a warning dialog appears, click Confirm to finalize your changes"* — the message is about the change invalidating existing provisioning profiles), click **Confirm**.
    - **This warning is EXPECTED, not an error.** It is exactly why Part B is required: adding a capability invalidates any existing provisioning profile for this App ID, so a fresh profile must be generated (Part B, and the callout in Part C).
    - **To double-check it worked:** re-open **com.cortex.doppl** and confirm, on the **Capabilities** tab, that **Sign in with Apple** is checked/enabled.

---

## PART B — Create a "Mac App Store Connect" distribution provisioning profile for com.cortex.doppl

15. In the left sidebar, click **Profiles**.
    - **You should SEE:** a table of existing profiles (may be empty). Top-left there's a blue **"+"** button (Register a New Provisioning Profile / Generate a profile).
16. Click the **"+"** button.
    - **You should SEE:** a page titled "Register a New Provisioning Profile" with radio-button groups, split into a **Development** section and a **Distribution** section.
17. Under **Distribution**, select the radio button labeled **"Mac App Store Connect"**.
    - This is the current exact label for the Mac App Store distribution profile (confirmed in Apple's official help: *"For macOS apps… select Mac App Store Connect"*). Do NOT pick **"Developer ID"** (that's for distributing OUTSIDE the Mac App Store and will be rejected by App Store Connect), and do NOT pick anything under **Development**.
18. Click **Continue** (bottom right).
    - **You should SEE:** a page asking you to choose an App ID, with an **App ID pop-up menu (dropdown)**.
19. Open the **App ID** dropdown and select **com.cortex.doppl** (it shows as your app name plus the bundle ID `com.cortex.doppl`).
    - **Do NOT** pick a wildcard entry such as one starting with **"XC "** or containing a **"\*"** — a wildcard profile cannot carry the Sign-in-with-Apple entitlement you just enabled. You need the explicit `com.cortex.doppl`.
20. Click **Continue**.
    - **You should SEE:** a "Select Certificates" page listing your distribution certificate(s), each with a checkbox.
21. Check the certificate that is your Mac App Store **app-signing** certificate.
    - **Naming note (this trips people up):** Apple's console has moved to the modern name **"Apple Distribution: Sarp Doven (P4H96J3VXY)"**, while older/legacy tooling calls the very same identity **"3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)"** — the cert you told me you already own. In today's portal it will most likely appear as **"Apple Distribution: Sarp Doven (P4H96J3VXY)"**. Select that. If BOTH names somehow appear, select **"3rd Party Mac Developer Application"** (the one you confirmed you have). Either way it's the same Mac App Store app-signing identity.
    - **If the certificate row is greyed out or shows "Expired":** the cert has expired and you must renew it first under **Certificates → "+"** before it becomes selectable here.
    - **Heads-up (silent surprise LATER, not here):** the Mac App Store also requires a *second* certificate — a **"Mac Installer Distribution"** cert (legacy name **"3rd Party Mac Developer Installer"**) — to sign the `.pkg` installer you eventually upload. That installer cert is **NOT** selected on this page and is **NOT** part of the provisioning profile. You don't need it to finish Part B, but if your later upload/build fails complaining about a missing *installer* signing identity, that's what it means — create it under **Certificates → "+" → "Mac Installer Distribution"**.
22. Click **Continue**.
    - **You should SEE:** a page with a **"Provisioning Profile Name"** text field.
23. In **Provisioning Profile Name**, type a clear label, e.g. **`Doppl Mac App Store`**. (The name is just a label; it doesn't affect functionality, but a clear name avoids confusion later.)
24. Click **Generate**.
    - **You should SEE:** a confirmation page showing the generated profile: its name (`Doppl Mac App Store`), the App ID (`com.cortex.doppl`), the type (Mac App Store / Distribution), and a **Download** button.
25. Click **Download**.
    - **You should SEE:** a file download to your Mac's **Downloads** folder with a **`.provisionprofile`** extension (e.g. `Doppl_Mac_App_Store.provisionprofile`). Apple confirms macOS profiles use **`.provisionprofile`**, NOT **`.mobileprovision`** (`.mobileprovision` is the iOS extension). If you were expecting a `.mobileprovision`, don't worry — `.provisionprofile` is correct for a Mac app, nothing failed.
    - If your browser opens/previews the file instead of downloading it, right-click the **Download** link and choose "Download Linked File" / "Save Link As".
    - **You should also SEE** the new profile listed back on the **Profiles** page with status **"Active"**.

---

## PART C — What to do with the downloaded file, and the "must regenerate" rule

26. **Where the file goes (to plug into `CORTEX_MAS_PROFILE`):** the downloaded `.provisionprofile` is what your build tooling references via the `CORTEX_MAS_PROFILE` variable. Practically:
    - Move it out of **Downloads** to a stable location, e.g. `~/Doppl/signing/Doppl_Mac_App_Store.provisionprofile`, so it can't be accidentally deleted.
    - Set `CORTEX_MAS_PROFILE` to that **absolute path** in your build environment/script.
    - **This is a local build/signing artifact only.** It has NOTHING to do with the hosted backend: do NOT copy this file to the server, do NOT put it in `/etc/cortex/cortex.env`, and do NOT run `systemctl restart cortex-api` for it. (Those are for the separate Google OAuth backend work — `ssh root@5.78.188.95`, edit `/etc/cortex/cortex.env`, then `systemctl restart cortex-api` — and are unrelated to this Apple profile.)

27. **CRITICAL — the "always regenerate after adding a capability" rule:** any provisioning profile that existed for `com.cortex.doppl` **before** you enabled Sign in with Apple in Part A is now **invalid** and does NOT contain the Sign-in-with-Apple entitlement. Old profiles do **not** auto-update. So:
    - The profile you just made in Part B (after enabling the capability) is correct — it includes the new entitlement.
    - If you had downloaded/embedded any earlier `com.cortex.doppl` Mac App Store profile, **discard it and use the new one.** Building with a stale profile causes signing/validation failures like *"profile doesn't include the com.apple.developer.applesignin entitlement."*
    - Rule of thumb: any time you change capabilities on the App ID in the future, come back to **Profiles**, regenerate, and re-download.

28. **Does Xcode "Automatically manage signing" remove the need for this manual profile?** — Yes, but only IF you build/archive through Xcode with **"Automatically manage signing"** turned ON (Xcode → your target → **Signing & Capabilities** tab → check **"Automatically manage signing"**). In that mode Xcode creates/refreshes the Mac App Store profile for you.
    - **BUT your Doppl build uses a MANUAL profile** — that's the entire reason `CORTEX_MAS_PROFILE` exists (it points at a hand-supplied profile). For a manual/scripted build, Xcode is NOT managing signing for you, so you **must** provide the profile from Part B. Do NOT skip Part B.
    - Also note: even in automatic mode you still had to do Part A. Xcode's automatic signing picks up capabilities that are already enabled on the App ID; for Sign in with Apple (which needs the primary-App-ID configuration) it does **not** enable/configure it on the portal for you.

---

## Quick sanity checklist (what "done" looks like)
- App ID `com.cortex.doppl` → **Capabilities** tab → **Sign in with Apple** is checked/enabled, configured as **primary**, server-to-server endpoint left blank. (Part A)
- Profiles list contains an **Active** profile named `Doppl Mac App Store`, type **Mac App Store Connect**, App ID `com.cortex.doppl`, using your Mac App Store app-signing cert (shown as **"Apple Distribution: Sarp Doven (P4H96J3VXY)"**, a.k.a. legacy "3rd Party Mac Developer Application"). (Part B)
- A **`.provisionprofile`** file is downloaded, moved to a stable absolute path, and `CORTEX_MAS_PROFILE` points to it. (Part C)
- No pre-existing/older `com.cortex.doppl` profile is being used anywhere. (Part C, step 27)
- (If your later `.pkg` upload complains about signing: you also need a **Mac Installer Distribution** cert — separate from the profile. See step 21 heads-up.)
---

# PART 3 — App Store Connect: create the app + submit for review

_Different website from developer.apple.com. Go to **https://appstoreconnect.apple.com**, sign in with the same Apple ID (Account Holder/Admin of team P4H96J3VXY). Some of this you can do before the build is ready; the upload (3D) needs the signed `.pkg` I'll produce for you._

## 3A — Create the app record
1. Go to **https://appstoreconnect.apple.com** -> sign in -> click **Apps**.
2. Click the blue **"+"** (top-left) -> **New App**.
3. In the dialog:
   - **Platforms:** check **macOS**.
   - **Name:** `Doppl` (this is the PUBLIC App Store name and must be unique store-wide; if "Doppl" is taken you'll be told — pick a close variant, e.g. "Doppl — Memory").
   - **Primary Language:** English (U.S.) (or your choice).
   - **Bundle ID:** pick **com.cortex.doppl** from the dropdown (it's there because you registered it on developer.apple.com).
   - **SKU:** any private internal code, e.g. `doppl-macos-001` (users never see it).
   - **User Access:** Full Access (default).
4. Click **Create**. You should SEE the app's main page in App Store Connect.

## 3B — App Privacy (the "nutrition label") — must match what the app actually collects
1. Left sidebar -> **App Privacy** -> **Get Started** (or **Edit**).
2. "Do you collect data from this app?" -> **Yes**.
3. Add these **three** data types (**+ Add Data Type** / **Set Up Data Types**):
   - **Contact Info -> Email Address**
   - **Contact Info -> Name**
   - **User Content -> Other User Content**  (this is the memory / notes users save)
4. For **EACH** of the three, answer the follow-ups **identically**:
   - **Is this data used to track you?** -> **No**
   - **Is this data linked to the user's identity?** -> **Yes, it is linked to the user**
   - **What is it used for?** -> check **App Functionality** (only)
5. **Publish** the privacy details. This exactly matches the app's `PrivacyInfo.xcprivacy` (Email + Name + User Content; Linked; not tracking; App Functionality) — keeping them consistent is what avoids a privacy rejection.

## 3C — Version metadata + the reviewer's demo login (critical for an account app)
1. Left sidebar -> your version (**"1.0 Prepare for Submission"**).
2. Fill the required fields: **Description**, **Keywords**, **Support URL** (e.g. https://trydoppl.com), **Category** (Productivity), and at least one **screenshot** (macOS screenshots are REQUIRED — take them from the running app; you literally cannot submit without one).
3. Scroll to **App Review Information** and set:
   - **Sign-In required:** toggle **ON**.
   - **User name:** `review@trydoppl.com`
   - **Password:** the demo-account password (the strong one generated earlier).
   - **Notes:** paste the contents of `docs/APP_REVIEW_NOTES.md` (it answers every prior rejection + explains the account requirement + gives the backend address).
   - Fill your **contact** name / email / phone.

## 3D — Upload the signed build (.pkg)
_I produce the signed `.pkg` for you (Part 5 of the plan) once you send me the provisioning profile. Then you upload it — easiest via Transporter:_
1. Install **Transporter** (free, by Apple) from the Mac App Store.
2. Open **Transporter** -> sign in with your Apple ID.
3. **Drag** the signed `Doppl-AppStore-<version>.pkg` into the window -> click **Deliver**.
4. Wait for **"Delivered"**. The build then takes ~15-60 min to finish processing in App Store Connect.
   - _(Command-line alternative if you prefer: `xcrun altool --upload-app -f <pkg> -t macos -u <apple-id> -p <app-specific-password>`.)_
5. Back in App Store Connect -> your version -> **Build** section -> click **+ / Select a build** -> pick the processed build.

## 3E — Export compliance + submit
1. **Export compliance** question ("Does your app use encryption?"): the app's Info.plist sets **ITSAppUsesNonExemptEncryption = false**, so answer **No** (standard HTTPS is exempt and the plist pre-answers this).
2. Answer **Content Rights** (you own/licensed the content) and **IDFA / advertising** (No — the app uses no advertising identifier).
3. Click **Add for Review** / **Submit for Review** (top-right).
4. Status -> **Waiting for Review**. Apple usually reviews a macOS app in ~1-3 days.

**Already handled for you (just confirm they're true):** working demo login (3C), Sign in with Apple offered first (build 19), privacy label matches the manifest (3B), no runnable/downloaded code + a proper Quit menu (in the app), and the backend address is in the notes (2.1a).

---

# PART 4 — What to send me (the hand-back)

Send me these and I finish the wiring — you don't touch the server or the build tooling:

1. **Google (from Part 1):** the **Client ID** (ends `.apps.googleusercontent.com`) + **Client Secret** (starts `GOCSPX-`). -> I set them on the server + restart + verify "Continue with Google" works. ~1 minute.
2. **Apple (from Part 2):** the downloaded **`.provisionprofile`** file (attach it, or tell me the full path if it's on this Mac). -> I run the packager with your two existing certs + this profile and produce the **signed, uploadable `Doppl-AppStore-*.pkg`** + its checksum, then hand it back for the Transporter upload (3D).

Then the only thing left on your side is App Store Connect (Part 3) — and I'll walk you through each screen live when you're there.

---

# Appendix — Creating the Installer certificate from scratch (ONLY if on a new Mac)

_You do NOT need this on your current Mac (both certs are already present). Keep it only in case you ever build on a different machine where `security find-identity -v -p basic` does not list a "3rd Party Mac Developer Installer" line._

# Get the Mac Installer Distribution signing certificate for submitting Doppl to the Mac App Store

> **READ THIS FIRST — you are almost certainly already done on THIS Mac.**
> The certificate you're being asked to create shows up in the Keychain (and in `productbuild`) under the name **`3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)`**. On the developer.apple.com website it is called **"Mac Installer Distribution"** — Apple uses two different names for the *same thing* (the website name and the Keychain name differ; this is confirmed in Apple's own current docs). On the Mac where this checklist was written, that installer identity **is already present and valid** — this was verified live while writing this:
>
> ```
> $ security find-identity -v -p basic
>   1) 6050CDB5D45DE288BA902F6E0CA8465168FD5015 "3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)"
>   2) 46B9E65414D8BE982B3B05024DE1B3AB39FE0A74 "3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)"   ← the installer cert
>      2 valid identities found
> ```
>
> **So on THIS Mac you can skip Parts A–C entirely and jump to Part D.** Run the command in **Part D, Step 1** first. If you see a line 2 like the one above (a "3rd Party Mac Developer Installer" line for team P4H96J3VXY), you already have the certificate — go straight to Part D. Only do Parts A–C if that command does **not** list a "3rd Party Mac Developer Installer" line (e.g. you're setting this up on a brand-new Mac, or the cert was deleted). Everything below is written so you can also do it from scratch on any Mac.

---

## Background: which certificate, and why (30-second read)

Submitting a Mac app to the Mac App Store needs **two different** signing certificates:

| What it signs | Website name (developer.apple.com) | Keychain / `productbuild` name |
|---|---|---|
| The **app** (`Doppl.app`) — you **already have this** | Apple Distribution / Mac App Distribution | **3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)** ✅ |
| The **installer** (`.pkg`) — this is the one this doc is about | **Mac Installer Distribution** | **3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)** ← THIS |

- Confirmed against Apple's current (mid-2026) certificate list: **"Mac Installer Distribution"** is described as *"Sign and submit a Mac Installer Package, containing your signed app, to the Mac App Store."* That is the one you want.
- An **installer** signing identity is **not** a code-signing identity, so it is normal (and correct) that it does **not** appear when you run `security find-identity -v -p codesigning` — it only shows up with `-p basic`. (Verified live on this Mac: `-p codesigning` shows only the *Application* cert; `-p basic` shows both.) Don't panic that the app cert shows under `codesigning` and the installer one doesn't; that's expected, not a failure.
- **Do NOT pick "Apple Distribution", "Mac App Distribution", "Developer ID Application", or "Developer ID Installer" for this.** Those are for other purposes:
  - *Developer ID Installer* = signing a `.pkg` you distribute **outside** the App Store (a downloadable installer). Apple's own description: *"Sign and distribute a Mac Installer Package, containing your signed app, outside the Mac App Store."* **Wrong** for the App Store — and it's the most common wrong click.
  - *Apple Distribution* / *Mac App Distribution* = signs the **app**, which you already have.
  - The only correct pick for a Mac App Store **installer** is **"Mac Installer Distribution"**.
- **Role requirement (confirmed in Apple docs):** distribution certificates **"can only be requested by Account Holders and Admins."** Since you're the founder who enrolled Team **P4H96J3VXY**, you are the Account Holder — you're fine. A "Developer" or "App Manager" role would **silently not see the "+"** create option or get a permissions error; if that happens, it's a role problem, not a bug.
- Certs are tied to the **Team (P4H96J3VXY)**. Create them while signed in with the Apple ID that owns that team, with that team selected in the top-of-page team switcher.

---

## PART A — Create the Certificate Signing Request (CSR) in Keychain Access

You do this on the Mac. It creates a private key locally and a request file to upload to Apple. Keep this Mac — the private key never leaves it, and it's what makes the downloaded certificate usable. (These steps match Apple's official "Create a certificate signing request" help page.)

1. Open **Keychain Access**. Press **Cmd+Space**, type `Keychain Access`, press **Return**. (It lives in `/Applications/Utilities/`.) You should SEE a window titled "Keychain Access" with a left sidebar listing keychains (login, System, etc.).

2. In the top menu bar, click **Keychain Access** → **Certificate Assistant** → **Request a Certificate from a Certificate Authority…**
   - You should SEE a new dialog titled **"Certificate Assistant"** with a "Certificate Information" form.
   - **If that menu item is greyed out:** click anywhere inside the Keychain Access window first (so Keychain Access is the frontmost app), then try again.

3. Fill in the form **exactly** like this:
   - **User Email Address:** your Apple Developer account email (`sarptan.doven1@gmail.com`). This is cosmetic; use the account email.
   - **Common Name:** a label you'll recognize, e.g. `Doppl Mac Installer` (this is just a name for the key pair on your Mac; it does **not** have to match anything at Apple).
   - **CA Email Address:** **leave this field completely EMPTY.** (Apple's own instructions say "Leave the CA Email Address field empty." If you type something here, it tries to email a CA and the flow breaks.)
   - **Request is:** select the radio button **"Saved to disk"**.
   - Leave **"Let me specify key pair information"** **UNCHECKED**. The default Keychain Access produces (RSA 2048) is exactly what Apple wants for this certificate. *(Only special certs like Apple Pay Payment Processing need a custom key pair — ECC 256 — and this is not one of them.)*

4. Click **Continue** (bottom-right of the dialog).
   - You should SEE a standard macOS **Save** sheet.

5. In the Save sheet:
   - **Save As:** leave the default `CertificateSigningRequest.certSigningRequest` (or rename to `DopplInstaller.certSigningRequest` — the name doesn't matter, but keep the `.certSigningRequest` extension).
   - **Where:** choose **Desktop** (easy to find later).
   - Click **Save**.

6. You should SEE a "Conclusion" screen saying the certificate request was created on disk. Click **Done**.
   - **Verify it worked:** on your **Desktop** there is now a file ending in **`.certSigningRequest`**. Also, in Keychain Access, select the **login** keychain → **Keys** category → you'll SEE a new **private key** entry named after your Common Name (`Doppl Mac Installer`). This private key is essential — do not delete it.

> **Silent-failure watch:** If no `.certSigningRequest` file appears on the Desktop, you either cancelled the Save sheet or typed something in "CA Email Address". Redo Part A. A CSR with a matching private key in your login keychain is required for the downloaded certificate to be usable.

---

## PART B — Create the "Mac Installer Distribution" certificate on developer.apple.com

7. In a web browser go to **https://developer.apple.com/account** and click **Sign In** (or **Account**) with the Apple ID that owns Team **P4H96J3VXY** (your founder Apple Developer account).
   - You should SEE the "Account" landing page. If your Apple ID is a member of more than one team, make sure the team selector (near the top of the page) shows the team associated with **P4H96J3VXY** — otherwise the cert lands on the wrong team.

8. Click **Certificates, Identifiers & Profiles** (a card/link on the Account page).
   - *(You can also go directly to `https://developer.apple.com/account/resources/certificates/list`, but that direct URL will first bounce you to Apple's sign-in page — that's normal; sign in and it lands you on the certificates list.)*
   - You should SEE a page with a left sidebar. Click **Certificates** in that sidebar. It shows a list of your existing certificates — you should already see your **3rd Party Mac Developer Application** cert here.

9. **On the top left of the certificates list, click the add button — the blue "+"** (this is the exact control Apple's current docs name: *"On the top left, click the add button (+)."*).
   - You should SEE a page for choosing a certificate type, with types grouped under category headings such as **Software** and **Services**, each with a radio button.

10. Under the **Software** category, select the radio button labeled **"Mac Installer Distribution"**.
    - Its description reads: *"Sign and submit a Mac Installer Package, containing your signed app, to the Mac App Store."*
    - **DOUBLE-CHECK you did NOT pick:** "Apple Distribution", "Mac App Distribution", "Developer ID Application", or **"Developer ID Installer"**. That last one — *"…outside the Mac App Store"* — is the classic wrong click. The correct one for App Store installer signing is **"Mac Installer Distribution"** only.

11. Click **Continue** (top-right).
    - You should SEE an **"Upload a Certificate Signing Request"** step with a **"Choose File"** button.

12. Click **Choose File**. In the file picker, navigate to your **Desktop** and select the **`.certSigningRequest`** file you made in Part A (Step 5). Click **Choose** (or **Open**).
    - You should SEE the filename now shown next to the Choose File button.

> **Silent-failure watch:** If Apple rejects the file with an error like *"invalid certificate request"* or a key-size complaint, you likely uploaded the wrong file (e.g. an old `.cer`) or the CSR is malformed / too small a key. Re-generate the CSR in Part A (leave "Let me specify key pair information" unchecked so you get RSA 2048) and upload the fresh `.certSigningRequest`.

13. Click **Continue** (top-right).
    - You should SEE a **"Download Your Certificate"** page with the certificate's details and a **Download** button.

---

## PART C — Download the .cer and install it into your login keychain

14. Click **Download**.
    - A file with a **`.cer`** extension (named something like `mac_installer.cer` or `distribution.cer`) lands in your **Downloads** folder. You should SEE it in your browser's downloads.

15. In **Finder**, open **Downloads**, and **double-click** the downloaded **`.cer`** file.
    - **Keychain Access** opens (it may briefly flash). The certificate installs into your **login** keychain and appears in the **My Certificates** category (this is exactly what Apple's docs say happens).
    - You should SEE (in Keychain Access → **login** keychain → **My Certificates**) a new entry named **"3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)"**. Click the disclosure triangle next to it — it should reveal a **private key** underneath. That private key underneath is what confirms it paired correctly.

> **Silent-failure watch (most common problem):** If the entry appears under **"Certificates"** but NOT under **"My Certificates"**, and has **no private key** beneath it, then this `.cer` did not pair with the private key from Part A. This happens if you generated the CSR on a *different* Mac, or deleted the private key. Fix: create the CSR again (Part A) on THIS Mac and re-do Parts B–C. Do **not** try to export/import the `.cer` alone — the certificate is useless without its matching private key.

---

## PART D — Get the exact identity string and put it in the build config

This is the string `productbuild` needs and the string you place into the `CORTEX_APPSTORE_INSTALLER_IDENTITY` setting used by `macos/package_app_store.sh`.

16. Open **Terminal** (Cmd+Space → `Terminal` → Return) and run **exactly**:
    ```
    security find-identity -v -p basic
    ```
    - **Use `-p basic`, NOT `-p codesigning`.** The installer certificate is an *installer* identity, not a code-signing identity, so it will **not** show up under `-p codesigning`. (Verified live on this Mac.) That's normal, not a bug.
    - You should SEE a numbered list that includes a line ending with:
      ```
      "3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)"
      ```
    - To list only the installer identity, run:
      ```
      security find-identity -v -p basic | grep Installer
      ```
      On this Mac that prints hash `46B9E65414D8BE982B3B05024DE1B3AB39FE0A74`.

17. The **identity string** is the text **inside the quotes** — copy it verbatim, exactly:
    ```
    3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)
    ```
    (You may also use the 40-character hex hash to its left — e.g. `46B9E65414D8BE982B3B05024DE1B3AB39FE0A74` — which is unambiguous; but the human-readable name is what the docs and the script's own help use.)

18. **Where this string goes — LOCAL Mac only.** This is a **local Mac build** setting, **not** a server setting. Do **NOT** put it in `/etc/cortex/cortex.env` on the server, and do **NOT** run `systemctl restart cortex-api` for this — that file and command are for the hosted backend and have nothing to do with signing the installer. The installer identity is consumed by the local packaging script `macos/package_app_store.sh` via the environment variable **`CORTEX_APPSTORE_INSTALLER_IDENTITY`** (verified: the script reads it and, if unset, prints `installer identity  : <none>`). In the Terminal window where you build the App Store `.pkg`, export it **before** running the packager:
    ```
    export CORTEX_APPSTORE_INSTALLER_IDENTITY="3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)"
    ```
    Pair it with the app-signing identity you already have when you do a real signed build:
    ```
    export CORTEX_APPSTORE_SIGN_IDENTITY="3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)"
    ```
    *(The script accepts both the modern name "Apple Distribution Installer: … (P4H96J3VXY)" AND the legacy "3rd Party Mac Developer Installer: … (P4H96J3VXY)". Your existing cert is the legacy-named one, so use exactly the string from Step 17.)*

19. Verify the packager sees it. In the **same** Terminal window, run the App Store packaging script:
    ```
    /Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/package_app_store.sh
    ```
    - In the printed **"== MAS PACKAGING PLAN =="** block you should SEE:
      ```
      installer identity  : 3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)
      ```
    - If instead it prints `installer identity  : <none>` and lists *"Mac Installer Distribution certificate ('Apple Distribution Installer' or legacy '3rd Party Mac Developer Installer') — set CORTEX_APPSTORE_INSTALLER_IDENTITY"* under **MISSING**, that means the `export` in Step 18 wasn't active in the **same** Terminal window/session. Re-run the `export`, then re-run the script in that same window.

> **Why you must set the env var even though the cert is installed:** the script auto-detects identities using `security find-identity -v -p codesigning` (verified in the script source), and — as explained above — the installer cert never appears there. So auto-detection **cannot** find it and it will read `<none>` unless you set `CORTEX_APPSTORE_INSTALLER_IDENTITY` explicitly. This is expected; setting the variable is the intended path for the installer identity.

---

## Could Xcode's "Automatically manage signing" do all of this instead?

**Short answer: partly, and for your setup it's not worth switching to.**

- **What "Automatically manage signing" (Xcode → target → Signing & Capabilities) would do:** when you enable it and pick your Team (P4H96J3VXY), Xcode can create and renew the certificates and provisioning profiles needed for a **Mac App Store** build, including generating the **Mac Installer Distribution** certificate for you (it does its own CSR behind the scenes). For a pure Xcode-driven App Store build, it removes the manual portal steps.
- **Why it does NOT fit this project well:**
  1. **This project doesn't build/submit through Xcode's GUI.** It ships the App Store `.pkg` via the command-line script `macos/package_app_store.sh` + `productbuild`, which need a **named identity string** you pass in an environment variable. Automatic signing hides that identity inside Xcode's managed profiles; you'd still run `security find-identity -v -p basic` to read the string out — so it saves you almost nothing here and adds a dependency on Xcode's GUI.
  2. **Automatic signing only covers the App Store / development flavor.** This repo also produces a **Developer ID / notarized DMG** path (`package_release.sh`). Xcode's automatic management does **not** handle Developer-ID-outside-the-App-Store signing, so you'd have a split, half-managed setup — more confusing, not less.
  3. **It can quietly create duplicate/extra certificates** and, in team accounts, churn or revoke certs when limits are hit. Manual creation (Parts A–C) gives you one clear, named identity you control and reuse across builds and CI, matching how this project already works.
- **Recommendation:** since you already have **both** the app-signing and (per the check at the top) the installer identity in your Keychain, **stay manual** — just do Part D. Manual signing is the better fit for a script/`productbuild`-based pipeline and for the mixed App Store + Developer ID distribution this project uses. Reserve "Automatically manage signing" for when you're doing everything inside Xcode's GUI and only targeting the App Store.
