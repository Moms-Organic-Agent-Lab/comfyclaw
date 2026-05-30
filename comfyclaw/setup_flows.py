"""
setup_flows — install + OAuth flows for agent-backend CLIs.

These flows are driven from the ComfyUI panel over the WebSocket sync
server, so non-coders never have to touch a terminal.  Each flow runs a
short-lived subprocess and streams stdout back to the panel via
callbacks.  The flows are intentionally stateful (one per WebSocket
connection) so the panel can wire up Install → Sign in → Done with a
clean lifecycle.

Currently implemented:

* :class:`CliInstallFlow` — generic "bash -lc <pinned cmd>" installer for
  Claude Code (``curl | bash``), Codex (Homebrew or npm) and Gemini CLI
  (npm). Each install command is pinned in :data:`_INSTALL_COMMANDS` so the
  WebSocket payload can never override what we run.
* :class:`ClaudeAuthFlow` — drives ``claude auth login`` in
  paste-back mode: scrape the ``Open in browser:`` URL from stdout,
  forward it to the panel, accept the user-pasted redirect URL,
  feed it back over stdin.
* :class:`CodexAuthFlow` — drives ``codex login`` in either browser
  (default — captures the URL printed by codex's local 1455 server) or
  device-code mode (``codex login --device-auth`` — URL + 8-char code
  for headless setups).

All auth flows accept a ``force=True`` flag at start time that first runs
the backend's ``<binary> logout`` so the user can switch accounts without
having to drop to a terminal.

* :class:`GeminiLogoutFlow` — Gemini CLI has no non-TUI auth subcommand,
  so we can't drive a full re-login from here.  This flow does the logout
  half (deletes ``~/.gemini/oauth_creds.json`` plus the active-account
  pointer) and tells the user to run ``gemini`` once in a terminal to
  complete the new sign-in.

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
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .agent_backends.base import (
    _env_with_claude_path,
    _probe_claude_auth,
    _probe_codex_auth,
    _resolve_claude_bin,
)

log = logging.getLogger(__name__)


# ── Trusted installer commands — never accepted from the panel ───────────────
# Pinned here so the WebSocket payload can never override the shell command we
# end up running.  Each entry maps a backend id to an ordered list of candidate
# commands; we pick the first whose first token is on PATH.  This lets us pick
# Homebrew over npm on macOS, fall back to npm on Linux, etc.
_CLAUDE_INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"
_INSTALL_COMMANDS: dict[str, list[str]] = {
    "claude-code": [
        # The Anthropic installer auto-detects npm/native, so we just always
        # use it.  curl -fsSL fails fast on TLS errors / 404 / etc.
        _CLAUDE_INSTALL_CMD,
    ],
    "codex": [
        # Homebrew on macOS keeps the binary on a stable path that gets picked
        # up by ``which codex`` in subsequent terminals.  npm is the
        # everywhere-fallback.
        "brew install codex",
        "npm install -g @openai/codex",
    ],
    "gemini-cli": [
        "npm install -g @google/gemini-cli",
    ],
}


def _pick_install_command(backend: str) -> str | None:
    """Return the first install command for *backend* whose binary is on PATH.

    The first token of each command (``brew`` / ``npm`` / ``curl``) must be
    resolvable by :func:`shutil.which`; otherwise we try the next candidate.
    Returns ``None`` if no candidate's binary is installed.
    """
    candidates = _INSTALL_COMMANDS.get(backend, [])
    for cmd in candidates:
        first = cmd.strip().split(None, 1)[0]
        if shutil.which(first):
            return cmd
    return None


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


# Strip ANSI styling sequences (the SGR family — colours, bold, etc.) before
# forwarding subprocess output to the browser, which doesn't render them and
# would otherwise show literal junk like ``[90mDevice codes…[0m``.  We keep
# the URL- and code-matching regexes operating on this cleaned string too.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


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
            return bool(self._proc and self._proc.poll() is None and not self._stopped.is_set())

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


class CliInstallFlow(_BaseFlow):
    """Run a pinned ``bash -lc <cmd>`` installer for a CLI backend.

    The shell command is looked up from :data:`_INSTALL_COMMANDS` by the
    *backend* identifier — the caller cannot pass an arbitrary command.
    """

    name = "cli-install"

    def __init__(
        self,
        backend: str,
        on_line: LineCallback,
        on_complete: Callable[[bool, str], None],
    ) -> None:
        super().__init__()
        self._backend = backend
        self._on_line = on_line
        self._on_complete = on_complete
        self._reader: threading.Thread | None = None
        self._command: str = _pick_install_command(backend) or ""

    @property
    def command(self) -> str:
        return self._command

    @property
    def backend(self) -> str:
        return self._backend

    def start(self) -> None:
        """Spawn the installer subprocess. Returns immediately; output streams
        through ``on_line``; ``on_complete`` fires when the process exits."""
        if self.is_running:
            self._on_line("info", "[install] Already running")
            return

        if not self._command:
            tried = _INSTALL_COMMANDS.get(self._backend, [])
            hint = (
                f"None of {[c.split()[0] for c in tried]} is on PATH. "
                "Install one of them (Homebrew or Node.js) and try again."
                if tried
                else f"No installer is registered for {self._backend!r}."
            )
            self._on_line("error", hint)
            self._on_complete(False, hint)
            return

        try:
            self._proc = subprocess.Popen(
                ["bash", "-lc", self._command],
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
        self._on_line("info", f"$ {self._command}")
        self._reader = threading.Thread(
            target=self._pump,
            daemon=True,
            name=f"{self._backend}-install-pump",
        )
        self._reader.start()

    def _pump(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, ""):
                if self._stopped.is_set():
                    break
                line = _strip_ansi(raw.rstrip("\n"))
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


# Back-compat shim: older imports still expect the old class name + signature.
class ClaudeInstallFlow(CliInstallFlow):
    """Back-compat shim: same behaviour as ``CliInstallFlow("claude-code", …)``."""

    def __init__(
        self,
        on_line: LineCallback,
        on_complete: Callable[[bool, str], None],
    ) -> None:
        super().__init__("claude-code", on_line=on_line, on_complete=on_complete)


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

    def start(self, auth_method: str = "claudeai", force: bool = False) -> None:
        """Spawn ``claude auth login`` and tail stdout for the OAuth URL.

        ``auth_method`` is ``"claudeai"`` (subscription, default) or
        ``"console"`` (API billing).

        If ``force`` is true, run ``claude auth logout`` first so the CLI is
        guaranteed to launch the OAuth flow (and the user can switch accounts).
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

        if force:
            self._on_progress("info", "Logging out previous Claude session…")
            try:
                subprocess.run(
                    [binary, "auth", "logout"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    env=env,
                )
            except Exception as exc:  # noqa: BLE001
                # Logout failure is not fatal — surface it but proceed to login.
                self._on_progress("info", f"claude auth logout warning: {exc}")

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
        self._reader = threading.Thread(target=self._pump, daemon=True, name="claude-auth-pump")
        self._reader.start()

    def _pump(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, ""):
                if self._stopped.is_set():
                    break
                # Strip ANSI styling before doing anything — both URL matching
                # AND user-facing display benefit from cleaned text.
                line = _strip_ansi(raw.rstrip("\n"))
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
        """Pick the OAuth URL out of a single stdout line (already ANSI-stripped)."""
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

        Accepts either:

        1. A full callback URL (``.../oauth/code/callback?code=...&state=...``), or
        2. A raw authentication code string from Claude's "Authentication Code" page.

        Returns ``(ok, message)`` for the caller to relay back to the panel.
        """
        raw = (url or "").strip()
        if not raw:
            return False, "Paste the redirect URL or authentication code."

        payload = raw
        if raw.lower().startswith(("http://", "https://")):
            parsed = urlparse(raw)
            q = parse_qs(parsed.query or "")
            code = (q.get("code") or [""])[0].strip()
            state = (q.get("state") or [""])[0].strip()
            if not code:
                return (
                    False,
                    "That URL doesn't contain a `code` parameter. Did you copy the full address?",
                )
            # Newer Claude auth screens provide a code value to paste back
            # (`code#state`) rather than the full callback URL.
            payload = unquote(code)
            if state:
                payload += f"#{unquote(state)}"
        else:
            payload = unquote(raw).strip()

        if not payload:
            return False, "Invalid code. Please make sure the full code was copied."

        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return False, "Auth process is no longer running. Try Sign in again."

        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not deliver code to claude: {exc}"

        self._on_progress("info", "Submitted authentication payload to Claude CLI…")
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
# Codex auth flow (ChatGPT subscription via device-code)
# ─────────────────────────────────────────────────────────────────────────────


# Two stdout shapes we care about:
#
#  Browser flow (``codex login``, default — uses the local 1455 callback):
#    Starting local login server on http://localhost:1455.
#    If your browser did not open, navigate to this URL to authenticate:
#    https://auth.openai.com/oauth/authorize?…&originator=codex_cli_rs
#
#  Device-code flow (``codex login --device-auth`` — for headless servers):
#    To sign in, open https://auth.openai.com/device and enter code: ABCD-EFGH
#
# In both cases the URL we want is the one pointing at auth.openai.com /
# chatgpt.com — never the http://localhost:1455 status line.
_CODEX_AUTH_URL_RE = re.compile(
    r"(https?://(?:auth\.openai\.com|chatgpt\.com|openai\.com)\S*)",
    re.IGNORECASE,
)
_CODEX_DEVICE_CODE_RE = re.compile(
    r"code[^A-Za-z0-9]{0,8}([A-Z0-9]{4,}[- ]?[A-Z0-9]{4,})",
    re.IGNORECASE,
)


class CodexAuthFlow(_BaseFlow):
    """Drive ``codex login`` over a WebSocket in one of two modes.

    Lifecycle (``mode="browser"`` — default):
      1. :meth:`start` spawns ``codex login`` (no flags).  Codex starts a
         local OAuth callback server on http://localhost:1455 and prints the
         authorize URL to stdout.
      2. We capture the authorize URL and forward it via ``on_url``.  The
         panel renders it as an "Open sign-in page" button.  The user signs
         in in their browser; OpenAI redirects to localhost:1455 which the
         spawned codex process is listening on.
      3. ``codex login`` writes ``~/.codex/auth.json`` and exits 0.  We
         re-probe with :func:`_probe_codex_auth` and fire ``on_complete``.

    Lifecycle (``mode="device_code"`` — for headless servers):
      1. :meth:`start` spawns ``codex login --device-auth``.  Codex prints
         the URL plus a short user code, then polls auth.openai.com until
         the device-flow approval succeeds.
      2. The reader forwards URL + code (``on_url`` + ``on_progress`` with
         ``level="code"``).  The panel shows both as copy-paste affordances.

    Both modes accept a ``force=True`` start argument that runs
    ``codex logout`` first so the user can switch ChatGPT accounts.
    """

    name = "codex-auth"

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
        self._code_seen = False
        self._stdout_buffer: list[str] = []
        self._url_event = threading.Event()
        self._mode: str = "browser"

    def start(self, mode: str = "browser", force: bool = False) -> None:
        """Spawn the codex login subprocess.

        Args:
            mode: ``"browser"`` (default) drives ``codex login`` and lets the
                user sign in through their browser via localhost:1455.
                ``"device_code"`` drives ``codex login --device-auth`` for
                headless servers.
            force: When true, run ``codex logout`` first to clear cached
                creds — useful for switching ChatGPT accounts.
        """
        binary = shutil.which("codex") or ""
        if not binary:
            self._on_complete(
                False,
                "Codex CLI not on PATH. Install it from the Agents tab "
                "(brew install codex / npm i -g @openai/codex).",
            )
            return

        # `BROWSER=true` makes codex's "did the browser open?" stub no-op so
        # the URL stays on stdout where we can scrape it.  DISPLAY=""
        # prevents codex from trying xdg-open on Linux.
        env = {**os.environ, "DISPLAY": "", "BROWSER": "true"}

        if force:
            self._on_progress("info", "Logging out previous Codex session…")
            try:
                subprocess.run(
                    [binary, "logout"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    env=env,
                )
            except Exception as exc:  # noqa: BLE001
                self._on_progress("info", f"codex logout warning: {exc}")

        # Build the argv list per mode.  We accept the strings the JS layer
        # most plausibly sends; default to browser on anything unrecognised.
        normalised = (mode or "browser").lower().replace("-", "_")
        if normalised in ("device", "device_code", "devicecode"):
            argv = [binary, "login", "--device-auth"]
            self._mode = "device_code"
            launch_msg = "Launching Codex device-code sign-in…"
        else:
            argv = [binary, "login"]
            self._mode = "browser"
            launch_msg = "Launching Codex browser sign-in…"

        try:
            self._proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            self._on_complete(False, f"Failed to launch codex: {exc}")
            return

        self._started_at = time.time()
        # Surface the chosen mode in the server log too, so "wrong URL in
        # the panel?" can be diagnosed without strace.
        log.info("codex login launched: argv=%s mode=%s force=%s", argv, self._mode, force)
        self._on_progress("info", launch_msg)
        self._reader = threading.Thread(target=self._pump, daemon=True, name="codex-auth-pump")
        self._reader.start()

    def _pump(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, ""):
                if self._stopped.is_set():
                    break
                # Always strip ANSI before anything else — it prevents
                # `[90m…[0m` junk from leaking into the browser status line.
                line = _strip_ansi(raw.rstrip("\n"))
                if not line:
                    continue
                self._stdout_buffer.append(line)
                self._on_progress("stdout", line)

                if not self._url_seen:
                    m = _CODEX_AUTH_URL_RE.search(line)
                    if m:
                        url = m.group(1).rstrip(".,)")
                        self._url_seen = True
                        self._on_url(url)
                        self._url_event.set()

                # Device-code mode also prints a short user code we surface
                # as a copyable chip in the panel.  Browser mode never does.
                if self._mode == "device_code" and not self._code_seen:
                    m = _CODEX_DEVICE_CODE_RE.search(line)
                    if m:
                        self._code_seen = True
                        self._on_progress("code", m.group(1).strip())
        except Exception as exc:  # noqa: BLE001
            self._on_progress("error", f"Reader error: {exc}")
        finally:
            rc = proc.wait()
            self._finish(rc)

    def wait_for_url(self, timeout: float = 30.0) -> bool:
        return self._url_event.wait(timeout=timeout)

    def _finish(self, rc: int) -> None:
        if self._stopped.is_set():
            self._on_complete(False, "cancelled")
            return

        # The user-facing browser flow can succeed (codex exits 0) or fail
        # (rc != 0, e.g. token never approved → timeout). Re-probe either
        # way so the panel reflects the post-flow auth state.
        binary = shutil.which("codex") or ""
        if not binary:
            self._on_complete(False, "codex binary disappeared after login")
            return

        state, _method, detail = _probe_codex_auth(binary)
        if state == "ok":
            self._on_complete(True, detail or "Signed in")
            return

        tail = "\n".join(self._stdout_buffer[-5:]) or detail
        # Detect the specific failure mode where the npm-shipped codex JS
        # wrapper can't spawn its bundled native (Gatekeeper quarantine,
        # missing exec bit, etc.).  The stdout includes a Node.js spawn
        # error dump mentioning the native binary path under
        # ``codex-darwin-arm64/vendor/.../codex/codex``.  Surface a
        # clearer message + workaround when we recognise it.
        full_buf = "\n".join(self._stdout_buffer)
        lower = full_buf.lower()
        wrapper_spawn_failed = (
            "spawnargs" in lower and "codex-darwin" in lower or "codex-linux" in lower
        )
        if rc != 0:
            if wrapper_spawn_failed and self._mode == "browser":
                self._on_complete(
                    False,
                    "Codex's npm wrapper couldn't launch its bundled native "
                    "binary for the browser sign-in.  Two workarounds:\n"
                    "  • Try again in **device-code** mode (works around the "
                    "wrapper entirely), or\n"
                    "  • Reinstall via Homebrew: "
                    "`brew install codex` — that ships a single-binary "
                    "codex that avoids the npm spawn dance.",
                )
                return
            self._on_complete(
                False,
                f"codex login exited with code {rc}: {tail[-300:]}",
            )
        else:
            self._on_complete(False, f"Sign-in did not complete: {tail[-300:]}")


# ─────────────────────────────────────────────────────────────────────────────
# Gemini logout flow (delete cached OAuth creds)
# ─────────────────────────────────────────────────────────────────────────────


# Files Gemini CLI writes its Google OAuth session into.  We blow these away on
# logout; the user then runs ``gemini`` once in a terminal to launch a fresh
# OAuth dance.  We *don't* touch ``GEMINI.md`` / ``installation_id`` / history.
_GEMINI_CRED_FILES: tuple[str, ...] = (
    "oauth_creds.json",
    "google_accounts.json",
)


class GeminiLogoutFlow(_BaseFlow):
    """Synchronous "logout" for Gemini CLI — there is no real `gemini auth`,
    so we just delete the OAuth cache files.

    Despite inheriting from :class:`_BaseFlow` (for the registry plumbing),
    this flow doesn't spawn a subprocess — :meth:`start` completes in-line and
    fires ``on_complete`` immediately.
    """

    name = "gemini-logout"

    def __init__(
        self,
        on_progress: Callable[[str, str], None],
        on_complete: Callable[[bool, str], None],
    ) -> None:
        super().__init__()
        self._on_progress = on_progress
        self._on_complete = on_complete

    def start(self) -> None:
        gemini_dir = os.path.join(os.path.expanduser("~"), ".gemini")
        if not os.path.isdir(gemini_dir):
            self._on_complete(
                True,
                "No Gemini credentials cached. Run `gemini` to sign in.",
            )
            return

        removed: list[str] = []
        kept: list[str] = []
        errors: list[str] = []
        for fname in _GEMINI_CRED_FILES:
            path = os.path.join(gemini_dir, fname)
            if not os.path.exists(path):
                continue
            try:
                os.remove(path)
                removed.append(fname)
                self._on_progress("info", f"Removed ~/.gemini/{fname}")
            except OSError as exc:
                kept.append(fname)
                errors.append(f"{fname}: {exc}")
                self._on_progress("error", f"Could not remove ~/.gemini/{fname}: {exc}")

        if errors:
            self._on_complete(
                False,
                "Logged out partially. Could not remove: " + "; ".join(errors),
            )
            return

        detail = (
            f"Removed {', '.join(removed)}. "
            "Run `gemini` in a terminal to sign in with a different account."
            if removed
            else "No Gemini credentials cached. Run `gemini` to sign in."
        )
        self._on_complete(True, detail)


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
