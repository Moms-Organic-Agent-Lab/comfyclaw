"""
setup_flows — install + OAuth flows for agent-backend CLIs.

These flows are driven from the ComfyUI panel over the WebSocket sync
server, so non-coders never have to touch a terminal.  Each flow runs a
short-lived subprocess and streams stdout back to the panel via
callbacks.  The flows are intentionally stateful (one per WebSocket
connection) so the panel can wire up Install → Sign in → Done with a
clean lifecycle.

Currently implemented:

* :class:`ClaudeInstallFlow` — runs the official curl-piped installer
  ``curl -fsSL https://claude.ai/install.sh | bash``.
* :class:`ClaudeAuthFlow` — drives ``claude auth login`` in
  paste-back mode: scrape the ``Open in browser:`` URL from stdout,
  forward it to the panel, accept the user-pasted redirect URL,
  feed it back over stdin.

Both flows are thread-based (the WebSocket server runs on asyncio in a
background thread; flows run their own background reader threads and
push events back via thread-safe callbacks).  Cancellation is
cooperative: ``cancel()`` sends SIGTERM, then SIGKILL after a 2 s
grace period.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from .agent_backends.base import (
    _env_with_claude_path,
    _probe_claude_auth,
    _resolve_claude_bin,
)

log = logging.getLogger(__name__)


# ── Trusted installer command — never accepted from the panel ────────────────
# The installer URL is pinned here so the WebSocket payload can never override
# what we curl-pipe into bash.
_CLAUDE_INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"


# Regex that matches the line the Claude CLI prints when it can't auto-open
# the browser (which is always the case on a headless cluster node).  The
# CLI prints something like::
#
#   Browser didn't open? Use the url below to sign in
#   Open in browser: https://claude.ai/oauth/authorize?...
#
# We require the explicit prefix to avoid grabbing unrelated URLs the CLI
# might emit (release notes, support links, etc.).
_OAUTH_URL_RE = re.compile(
    r"open\s+in\s+browser:?\s*(https?://\S+)",
    re.IGNORECASE,
)
_PLAIN_URL_RE = re.compile(r"https?://\S+")
# When falling back to a bare URL line, only treat it as the OAuth target
# if its host or path actually looks like an authorize endpoint.
_AUTH_URL_HINTS: tuple[str, ...] = ("oauth", "authorize", "claude.ai", "anthropic.com")


LineCallback = Callable[[str, str], None]
"""``(level, text)`` where level is 'stdout' | 'stderr' | 'info' | 'error'."""


class _BaseFlow:
    """Common cancellation + bookkeeping plumbing."""

    name: str = "flow"

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._started_at: float = 0.0
        self._stopped: threading.Event = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(
                self._proc
                and self._proc.poll() is None
                and not self._stopped.is_set()
            )

    def cancel(self) -> None:
        """Best-effort terminate: SIGTERM, 2s grace, then SIGKILL."""
        with self._lock:
            proc = self._proc
            self._stopped.set()
        if not proc or proc.poll() is not None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass
        # Wait briefly for graceful exit
        for _ in range(20):  # 20 × 0.1s = 2 s
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Install flow
# ─────────────────────────────────────────────────────────────────────────────


class ClaudeInstallFlow(_BaseFlow):
    """Run the official Claude Code curl installer and stream output."""

    name = "claude-install"

    def __init__(
        self,
        on_line: LineCallback,
        on_complete: Callable[[bool, str], None],
    ) -> None:
        super().__init__()
        self._on_line = on_line
        self._on_complete = on_complete
        self._reader: threading.Thread | None = None

    @property
    def command(self) -> str:
        return _CLAUDE_INSTALL_CMD

    def start(self) -> None:
        """Spawn the installer subprocess. Returns immediately; output streams
        through ``on_line``; ``on_complete`` fires when the process exits."""
        if self.is_running:
            self._on_line("info", "[install] Already running")
            return

        try:
            self._proc = subprocess.Popen(
                ["bash", "-lc", _CLAUDE_INSTALL_CMD],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ},
            )
        except Exception as exc:  # noqa: BLE001
            self._on_line("error", f"Failed to launch installer: {exc}")
            self._on_complete(False, str(exc))
            return

        self._started_at = time.time()
        self._on_line("info", f"$ {_CLAUDE_INSTALL_CMD}")
        self._reader = threading.Thread(
            target=self._pump, daemon=True, name="claude-install-pump"
        )
        self._reader.start()

    def _pump(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, ""):
                if self._stopped.is_set():
                    break
                line = raw.rstrip("\n")
                if line:
                    self._on_line("stdout", line)
        except Exception as exc:  # noqa: BLE001
            self._on_line("error", f"Reader error: {exc}")
        finally:
            rc = proc.wait()
            ok = rc == 0 and not self._stopped.is_set()
            if self._stopped.is_set():
                self._on_complete(False, "cancelled")
            elif ok:
                self._on_complete(True, "")
            else:
                self._on_complete(False, f"installer exited with code {rc}")


# ─────────────────────────────────────────────────────────────────────────────
# Auth flow
# ─────────────────────────────────────────────────────────────────────────────


class ClaudeAuthFlow(_BaseFlow):
    """Drive ``claude auth login`` in paste-back mode over a WebSocket.

    Lifecycle:
      1. :meth:`start` spawns ``claude auth login --claudeai`` under a
         pipe with ``DISPLAY=""`` so the CLI takes the headless path
         (prints the OAuth URL on stdout, waits on stdin for the
         redirect URL).
      2. A background reader thread tails stdout. The first ``https://``
         URL we see is forwarded via ``on_url`` so the panel can open it
         in a new tab.
      3. The user signs in on claude.ai; the IdP redirects to a
         loopback URL the user's browser can't reach (we're on a remote
         node). They copy that URL from the address bar and submit it
         via :meth:`submit_redirect_url`.
      4. We write the URL + "\\n" to the CLI's stdin. The CLI completes
         PKCE and exits.
      5. We re-probe ``claude auth status --json`` and fire
         ``on_complete(success, detail)``.

    Cancel at any stage sends SIGTERM + SIGKILL as in :class:`_BaseFlow`.
    """

    name = "claude-auth"

    def __init__(
        self,
        on_url: Callable[[str], None],
        on_progress: Callable[[str, str], None],
        on_complete: Callable[[bool, str], None],
    ) -> None:
        super().__init__()
        self._on_url = on_url
        self._on_progress = on_progress  # (level, message)
        self._on_complete = on_complete
        self._reader: threading.Thread | None = None
        self._url_seen = False
        self._stdout_buffer: list[str] = []
        self._url_event = threading.Event()
        self._auth_method = "claudeai"

    def start(self, auth_method: str = "claudeai") -> None:
        """Spawn ``claude auth login`` and tail stdout for the OAuth URL.

        ``auth_method`` is ``"claudeai"`` (subscription, default) or
        ``"console"`` (API billing).
        """
        if self.is_running:
            self._on_progress("info", "Auth flow already running")
            return

        binary = _resolve_claude_bin()
        if not binary:
            self._on_complete(False, "claude binary not found; install first")
            return
        self._auth_method = auth_method

        # Force the CLI to take the paste-back path:
        #   * DISPLAY="" prevents the CLI from trying to call xdg-open.
        #   * BROWSER=true makes the open-browser stub a no-op so the
        #     CLI prints the URL and waits on stdin.
        # Also: the claude binary is a node script (`#!/usr/bin/env node`),
        # so co-locate its bin dir on PATH or the kernel will fail with
        # exit 127 / "node: No such file or directory".
        env = _env_with_claude_path(binary)
        env["DISPLAY"] = ""
        env["BROWSER"] = "true"

        flag = "--console" if auth_method == "console" else "--claudeai"
        try:
            self._proc = subprocess.Popen(
                [binary, "auth", "login", flag],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            self._on_complete(False, f"Failed to launch claude: {exc}")
            return

        self._started_at = time.time()
        self._on_progress("info", "Launching Claude sign-in…")
        self._reader = threading.Thread(
            target=self._pump, daemon=True, name="claude-auth-pump"
        )
        self._reader.start()

    def _pump(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, ""):
                if self._stopped.is_set():
                    break
                line = raw.rstrip("\n")
                if not line:
                    continue
                self._stdout_buffer.append(line)
                self._on_progress("stdout", line)

                if not self._url_seen:
                    url = self._extract_url(line)
                    if url:
                        self._url_seen = True
                        self._on_url(url)
                        self._url_event.set()
        except Exception as exc:  # noqa: BLE001
            self._on_progress("error", f"Reader error: {exc}")
        finally:
            rc = proc.wait()
            self._finish(rc)

    @staticmethod
    def _extract_url(line: str) -> str:
        """Pick the OAuth URL out of a single stdout line."""
        # The CLI may emit ANSI styling; strip it before matching.
        line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
        m = _OAUTH_URL_RE.search(line)
        if m:
            return m.group(1).rstrip(".,)")
        m = _PLAIN_URL_RE.search(line)
        if m:
            url = m.group(0).rstrip(".,)")
            low = url.lower()
            if any(hint in low for hint in _AUTH_URL_HINTS):
                return url
        return ""

    def wait_for_url(self, timeout: float = 30.0) -> bool:
        """Block until the OAuth URL is captured or timeout."""
        return self._url_event.wait(timeout=timeout)

    def submit_redirect_url(self, url: str) -> tuple[bool, str]:
        """Forward the user's pasted redirect URL into the CLI's stdin.

        Validates that the input looks like a URL containing a ``code``
        query parameter. Returns ``(ok, message)`` for the caller to
        relay back to the panel.
        """
        url = (url or "").strip()
        if not url:
            return False, "Paste the URL you were redirected to."
        if not url.lower().startswith(("http://", "https://")):
            return False, "URL must start with http:// or https://"
        if "code=" not in url:
            return False, "That URL doesn't contain a `code` parameter. Did you copy the full address?"

        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False, "Auth process is no longer running. Try Sign in again."

        try:
            proc.stdin.write(url + "\n")
            proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not deliver code to claude: {exc}"

        self._on_progress("info", "Submitted redirect URL to Claude CLI…")
        return True, "submitted"

    def _finish(self, rc: int) -> None:
        if self._stopped.is_set():
            self._on_complete(False, "cancelled")
            return
        if rc != 0 and not self._url_seen:
            # Process died before we could even forward a URL.
            tail = "\n".join(self._stdout_buffer[-10:]) or "no output"
            self._on_complete(False, f"claude exited with code {rc}: {tail[-300:]}")
            return

        # Verify with the same probe we use in BackendStatus.
        binary = _resolve_claude_bin()
        if not binary:
            self._on_complete(False, "claude binary disappeared after login")
            return
        state, _method, detail = _probe_claude_auth(binary)
        if state == "ok":
            self._on_complete(True, detail or "Signed in")
        else:
            tail = "\n".join(self._stdout_buffer[-5:]) or detail
            self._on_complete(False, f"Sign-in did not complete: {tail[-300:]}")


# ─────────────────────────────────────────────────────────────────────────────
# Per-connection registry
# ─────────────────────────────────────────────────────────────────────────────


class SetupFlowRegistry:
    """Tracks at most one in-flight setup flow per WebSocket connection.

    Used by :class:`SyncServer` so it can cancel orphaned flows on
    disconnect and reject overlapping start requests.
    """

    def __init__(self) -> None:
        self._flows: dict[Any, _BaseFlow] = {}
        self._lock = threading.Lock()

    def get(self, ws: Any) -> _BaseFlow | None:
        with self._lock:
            return self._flows.get(ws)

    def set(self, ws: Any, flow: _BaseFlow) -> _BaseFlow | None:
        """Attach a flow to *ws*. Returns the previous one, if any."""
        with self._lock:
            prev = self._flows.get(ws)
            self._flows[ws] = flow
        return prev

    def pop(self, ws: Any) -> _BaseFlow | None:
        with self._lock:
            return self._flows.pop(ws, None)

    def cancel_all(self) -> None:
        with self._lock:
            flows = list(self._flows.values())
            self._flows.clear()
        for flow in flows:
            try:
                flow.cancel()
            except Exception:  # noqa: BLE001
                pass

    def cancel_for(self, ws: Any) -> None:
        flow = self.pop(ws)
        if flow:
            try:
                flow.cancel()
            except Exception:  # noqa: BLE001
                pass
