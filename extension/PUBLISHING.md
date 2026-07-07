# Publishing the Cortex browser extension

The extension is a complete Manifest V3 WebExtension (valid manifest, `node --check`-clean JS,
loadable unpacked today). Store submission is a credentialed, review-gated action — do it from your
own developer accounts. Bump `version` in `manifest.json` before each submission.

## Package it
```bash
cd extension
zip -r ../cortex-extension-$(node -p "require('./manifest.json').version").zip . -x '*.DS_Store'
```

## Chrome Web Store (also serves Edge via the Chromium store)
1. Chrome Web Store Developer Dashboard → **New item** → upload the zip.
2. Fill listing: name "Cortex Memory", description (see README), a 128px icon + screenshots, category
   "Productivity", a privacy policy URL (state: talks only to the user's local Cortex; token in
   browser sync storage; no external servers). Justify permissions: `storage`, `activeTab`,
   `scripting`, and the loopback + site `host_permissions`.
3. Submit for review (first review can take a few days).

## Firefox Add-ons (AMO)
1. https://addons.mozilla.org → Developer Hub → **Submit a New Add-on** → upload the zip.
2. The `manifest.json` already includes a `browser_specific_settings.gecko` id for Firefox.
3. AMO runs an automated validator + human review; address any manifest warnings it flags.

## Safari (macOS)
Safari needs a native wrapper: run Apple's `xcrun safari-web-extension-converter extension/` to
generate an Xcode project, then build + notarize + submit through App Store Connect (or bundle the
Safari extension target inside the Cortex.app). This requires an Apple Developer account.

## After approval
- Update the install instructions in `extension/README.md` + `docs/EXTERNAL_INTEGRATIONS.md` with the
  store links (replacing "load unpacked").
- Keep the loopback-only + token privacy note prominent — it's the extension's core trust story.

Current limitation to note in the listing: the extension pulls context and injects it (click ◆ Cortex);
server→extension push (an SSE `/v1/stream` channel) is a planned follow-up.
