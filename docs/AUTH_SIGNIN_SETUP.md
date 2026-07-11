# Sign-in pipelines — status + founder config

Diagnosis of "the Apple / GitHub sign-in buttons don't work," what was fixed in code, and the
config steps only the account owner can do to make each provider fully live.

## Root causes (verified)

- **Apple button was dead for two independent reasons, both config (not code):**
  1. The shipped app is **ad-hoc signed with zero entitlements** (`codesign -dv` → `Signature=adhoc`).
     `canUseNativeAppleSignIn` checks the `com.apple.developer.applesignin` entitlement at runtime;
     absent → the native `SignInWithAppleButton` was `.disabled()`. An unsigned/ad-hoc build **cannot**
     do native Sign in with Apple — an Apple platform requirement, not something code can bypass.
  2. The **hosted backend advertises only `google` + `github`** (`GET /v1/auth/providers`) — **no
     `apple`**. So even a correctly-signed build would fail at `/v1/auth/oauth/apple/native` because
     Apple OIDC isn't configured server-side.
- **GitHub actually worked server-side** (github is a configured provider), but the in-app sign-in
  form had **no labeled "Sign in with GitHub" button** — it was only reachable via a generic
  "Continue in browser," so users couldn't find a working GitHub button.

## What was fixed in code (shipped)

- **Provider-aware sign-in** (`CortexCloudAuth.swift`): the app now fetches `GET /v1/auth/providers`
  and renders a **real labeled button per configured provider** — "Sign in with GitHub", "Continue
  with Google". Each starts the browser app-flow handoff (session polled out, no token in a URL).
- **Apple button is shown only when it can actually work** — i.e. the build carries the applesignin
  entitlement **and** the backend advertises `apple`. Otherwise it's hidden (no dead disabled control).
- **One-hop provider handoff** (`webauth.py` `/account/login`): `...&provider=github` now sets the
  df_af binding cookie and 302-redirects straight to that provider's OAuth (only for configured
  providers → no open redirect). So "Sign in with GitHub" lands the user directly on GitHub.
- Regression tests added in `test_web_account.py` (one-hop redirect + unknown-provider guard).

**Net effect today:** with the current hosted backend, the app shows working **"Sign in with
GitHub"** and **"Continue with Google"** buttons plus email/password. The Apple button appears
automatically once the two steps below are done.

## Founder steps to make Apple sign-in live

1. **Sign the app with the Apple Sign In entitlement.** It's already in `macos/AppStore.entitlements`
   and `macos/DeveloperID.entitlements`; the ad-hoc dev build just doesn't apply them. Ship via
   either: the **MAS build** with a provisioning profile for `com.cortex.doppl` that has Sign in with
   Apple enabled, **or** a **Developer-ID-signed** DMG (`CORTEX_CODESIGN_IDENTITY=<Developer ID>` so
   `build.sh` applies `DeveloperID.entitlements`).
2. **Configure Apple as an OIDC provider on the hosted backend** (so `/v1/auth/providers` returns
   `apple` and `/v1/auth/oauth/apple/native` can verify id_tokens): create an Apple **Services ID** +
   **Sign in with Apple key** (Team ID + Key ID + .p8), and set the corresponding provider env vars in
   `/etc/cortex/cortex.env` server-side (client_id = the Services ID, plus the key material). Restart.

Do both and the native Apple button appears and works with zero further app changes.

## GitHub — fully working now, one optional item

- **Account sign-in with GitHub:** working (in-app button → GitHub OAuth → session). Confirm the
  GitHub OAuth app's **Authorization callback URL** matches `https://api.signindoppl.com/v1/auth/oauth/github/callback`.
- **GitHub as a data *source connector*** (device flow) is separate and needs **"Device Flow" enabled**
  on the GitHub OAuth app (Settings → the app → check *Enable Device Flow*). Not required for sign-in.
