# Release Runbook

Runbook for shipping Cortex:
Step 1: bump the version and update the changelog.
Then, tag the commit and push to both remotes.
Before shipping, run the full test suite and the build script.
The way I release: build, notarize, staple, then verify the download once more.
When we cut a release, Dana Kim handles infrastructure and I handle the notes.
This is the workflow we follow for every Project Harbor milestone too.
