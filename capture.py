"""
cortex/capture.py
-----------------
Global hotkey capture tool. Lives in your Mac menu bar.

Press Cmd+Shift+C anywhere to save selected text to Cortex.

How it works:
  1. You select text in any app (ChatGPT, Slack, browser, Notes, anywhere)
  2. Press Cmd+Shift+C
  3. It copies the selection to clipboard (simulates Cmd+C)
  4. Grabs the clipboard text
  5. Runs it through the Cortex pipeline (extract → GitHub → Redis)
  6. Shows a macOS notification with what was captured

Run: python capture.py
"""

import os
import sys
import time
import threading
import subprocess
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import rumps
import pyperclip
from pynput import keyboard

from ingest import extract_context, format_extraction_summary
from github_store import save_extracted_context

try:
    from redis_store import embed_and_store
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False


# ── Hotkey config ─────────────────────────────────────────────────────────────

HOTKEY = {keyboard.Key.cmd, keyboard.Key.shift, keyboard.KeyCode.from_char('v')}
current_keys = set()
hotkey_triggered = False


# ── Notification ──────────────────────────────────────────────────────────────

def notify(title: str, message: str):
    """Show a macOS notification."""
    subprocess.run([
        "osascript", "-e",
        f'display notification "{message}" with title "{title}"'
    ], capture_output=True)


# ── Core capture function ─────────────────────────────────────────────────────

def capture_and_save(app_instance=None):
    """
    Grab selected text and save to Cortex.
    Called when hotkey is triggered.
    """
    # Update menu bar icon to show we're working
    if app_instance:
        app_instance.title = "🧠 ..."

    try:
        # Just read whatever is currently in the clipboard
        # Flow: user selects text → Cmd+C → then Cmd+Shift+V to save to Cortex
        selected_text = pyperclip.paste()

        if not selected_text or not selected_text.strip():
            notify("Cortex", "⚠️ Clipboard is empty — copy something first (Cmd+C)")
            if app_instance:
                app_instance.title = "🧠"
            return

        if len(selected_text.strip()) < 20:
            notify("Cortex", "⚠️ Too short to capture")
            if app_instance:
                app_instance.title = "🧠"
            return

        # Detect source app
        source = detect_source_app()
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Capturing from {source}")
        print(f"Text: {selected_text[:100]}{'...' if len(selected_text) > 100 else ''}")

        # Extract context with Claude
        extracted = extract_context(selected_text, source=source)
        summary = format_extraction_summary(extracted)
        print(summary)

        # Save to GitHub
        saved_files = save_extracted_context(extracted, raw_text=selected_text)

        # Embed in Redis
        if REDIS_AVAILABLE:
            try:
                embed_and_store(extracted, raw_text=selected_text)
            except Exception:
                pass

        # Build notification message
        counts = []
        if extracted.get("KEY_INSIGHTS"):
            counts.append(f"{len(extracted['KEY_INSIGHTS'])} insights")
        if extracted.get("DECISIONS"):
            counts.append(f"{len(extracted['DECISIONS'])} decisions")
        if extracted.get("OPEN_QUESTIONS"):
            counts.append(f"{len(extracted['OPEN_QUESTIONS'])} questions")
        if extracted.get("ACTION_ITEMS"):
            counts.append(f"{len(extracted['ACTION_ITEMS'])} actions")

        msg = ", ".join(counts) if counts else "summary saved"
        notify("✅ Cortex", f"Captured from {source}: {msg}")
        print(f"✅ Saved {len(saved_files)} files to GitHub")

    except Exception as e:
        notify("Cortex", f"❌ Error: {str(e)[:60]}")
        print(f"Error: {e}")

    finally:
        if app_instance:
            app_instance.title = "🧠"


def detect_source_app() -> str:
    """Try to detect which app is frontmost."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True
        )
        app_name = result.stdout.strip().lower()

        # Map common apps to source labels
        app_map = {
            "google chrome": "chrome",
            "safari": "safari",
            "firefox": "firefox",
            "slack": "slack",
            "notes": "apple-notes",
            "messages": "imessage",
            "mail": "email",
            "notion": "notion",
            "arc": "arc",
        }
        for key, label in app_map.items():
            if key in app_name:
                return label
        return app_name or "unknown"
    except Exception:
        return "unknown"


# ── Menu bar app ──────────────────────────────────────────────────────────────

class CortexApp(rumps.App):
    def __init__(self):
        super().__init__("🧠", quit_button="Quit Cortex")
        self.menu = [
            rumps.MenuItem("Cortex — Universal Context Layer", callback=None),
            None,  # separator
            rumps.MenuItem("Capture selection (Cmd+Shift+C)", callback=self.manual_capture),
            None,
            rumps.MenuItem("Open GitHub repo", callback=self.open_repo),
            rumps.MenuItem("Status", callback=self.show_status),
        ]
        # Start global hotkey listener in background thread
        self.listener_thread = threading.Thread(target=self.start_hotkey_listener, daemon=True)
        self.listener_thread.start()
        print("🧠 Cortex capture running. Press Cmd+Shift+C anywhere to capture selected text.")

    def start_hotkey_listener(self):
        pressed = set()

        def on_press(key):
            pressed.add(key)
            # Check if Cmd+Shift+C is all held
            if (
                keyboard.Key.cmd in pressed
                and keyboard.Key.shift in pressed
                and keyboard.KeyCode.from_char('v') in pressed
            ):
                # Run capture in a separate thread to not block the listener
                threading.Thread(target=capture_and_save, args=(self,), daemon=True).start()
                pressed.clear()  # prevent repeat triggers

        def on_release(key):
            pressed.discard(key)

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            listener.join()

    @rumps.clicked("Capture selection (Cmd+Shift+C)")
    def manual_capture(self, _):
        threading.Thread(target=capture_and_save, args=(self,), daemon=True).start()

    @rumps.clicked("Open GitHub repo")
    def open_repo(self, _):
        repo = os.environ.get("CORTEX_REPO", "")
        if repo:
            subprocess.run(["open", f"https://github.com/{repo}"])

    @rumps.clicked("Status")
    def show_status(self, _):
        redis_status = "✅ connected" if REDIS_AVAILABLE else "❌ not running"
        repo = os.environ.get("CORTEX_REPO", "not set")
        rumps.alert(
            title="Cortex Status",
            message=f"Redis: {redis_status}\nRepo: {repo}\nHotkey: Cmd+Shift+C"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    CortexApp().run()
