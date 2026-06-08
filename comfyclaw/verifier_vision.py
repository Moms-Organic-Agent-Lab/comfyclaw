"""
Vision completion adapters for the verifier.

The verifier needs to send an image + a text instruction to a model and get
text back (which it then parses as JSON).  Historically this was hard-wired to
``litellm.completion`` — meaning a user on a CLI *subscription* (Codex /
Claude Code) with no API key could build a workflow with their subscription
but the verifier would then fail with ``litellm.AuthenticationError: Missing
Anthropic API Key``.

This module decouples *how* a vision request is made from *what* the verifier
asks.  Two implementations:

* :class:`LiteLLMVision` — the original path, any vision model via LiteLLM
  (needs a provider/model string + API key).
* :class:`CliVision` — drives the ``codex`` or ``claude`` CLI in headless mode,
  reusing the user's subscription login.  The image is written to a temp file
  and attached (``codex exec -i <file>`` / a path the Claude Read tool ingests).

All adapters expose the same two methods so the verifier is agnostic:

    complete_text(prompt, max_tokens) -> str
    complete_with_image(b64_data, media_type, text, max_tokens) -> str
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from typing import Protocol, runtime_checkable

_EXT_BY_MEDIA = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


@runtime_checkable
class VisionCompleter(Protocol):
    """Transport for the verifier's text + image requests."""

    def complete_text(self, prompt: str, max_tokens: int = 200) -> str: ...

    def complete_with_image(
        self, b64_data: str, media_type: str, text: str, max_tokens: int = 1024
    ) -> str: ...


# ---------------------------------------------------------------------------
# LiteLLM transport (original behaviour)
# ---------------------------------------------------------------------------


class LiteLLMVision:
    """Vision via ``litellm.completion`` — needs a provider/model + API key."""

    def __init__(self, model: str) -> None:
        self.model = model

    def complete_text(self, prompt: str, max_tokens: int = 200) -> str:
        import litellm

        resp = litellm.completion(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()

    def complete_with_image(
        self, b64_data: str, media_type: str, text: str, max_tokens: int = 1024
    ) -> str:
        import litellm

        resp = litellm.completion(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64_data}"},
                        },
                        {"type": "text", "text": text},
                    ],
                }
            ],
        )
        return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# CLI transport (reuses Codex / Claude Code subscription)
# ---------------------------------------------------------------------------


class CliVisionError(RuntimeError):
    """Raised when the CLI vision call cannot produce usable output."""


class CliVision:
    """Vision via the ``codex`` or ``claude`` CLI in headless mode.

    Parameters
    ----------
    backend : ``"codex"`` or ``"claude-code"``.
    model   : the user's model selection (resolved per-backend).
    timeout : per-call subprocess timeout (seconds).
    """

    def __init__(self, backend: str, model: str = "", timeout: float = 180.0) -> None:
        self.backend = backend
        self.model = model
        self.timeout = timeout

    # -- public API --------------------------------------------------------

    def complete_text(self, prompt: str, max_tokens: int = 200) -> str:
        if self.backend == "codex":
            return self._run_codex(prompt, image_path=None)
        if self.backend in ("claude-code", "claude"):
            return self._run_claude_text(prompt)
        raise CliVisionError(f"CliVision: unsupported backend {self.backend!r}")

    def complete_with_image(
        self, b64_data: str, media_type: str, text: str, max_tokens: int = 1024
    ) -> str:
        if self.backend in ("claude-code", "claude"):
            # Claude Code has no --image flag, and feeding the image via its
            # agentic Read tool is slow (a full tool-use loop) and unreliable.
            # stream-json input delivers the base64 image directly in one shot.
            return self._run_claude_image(b64_data, media_type, text)

        # codex attaches a real file via ``-i``.
        suffix = _EXT_BY_MEDIA.get(media_type, ".png")
        raw = base64.standard_b64decode(b64_data)
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="comfyclaw_verify_")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            return self._run_codex(text, image_path=path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # -- backend dispatch --------------------------------------------------

    def _run_codex(self, prompt: str, image_path: str | None) -> str:
        from .agent_backends.codex_backend import (
            _codex_bin,
            _extract_agent_message_text,
        )
        from .chat_agent import _codex_pick_model

        argv = [
            _codex_bin(),
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-c",
            f'model="{_codex_pick_model(self.model)}"',
        ]
        # IMPORTANT: the prompt must come BEFORE ``-i``.  ``-i/--image`` is
        # variadic (``<FILE>...``); if the prompt follows it, codex greedily
        # treats the prompt string as another image filename and then reports
        # "No prompt provided via stdin".  Prompt-positional-first avoids that.
        argv.append(prompt)
        if image_path:
            argv += ["-i", image_path]

        env = {**os.environ, "RUST_LOG": "off", "CODEX_LOG_LEVEL": "error", "NO_COLOR": "1"}
        rc, out, err = self._exec(argv, env)
        parts = [_extract_agent_message_text(line) for line in out.splitlines() if line.strip()]
        parts = [p for p in parts if p]
        if parts:
            return parts[-1].strip()
        if rc != 0:
            raise CliVisionError(f"codex vision rc={rc}: {(err or out)[:300]}")
        raise CliVisionError("codex vision returned no agent_message")

    def _claude_setup(self) -> tuple[str, dict[str, str], str]:
        """Resolve the claude binary, its env, and a CLI-valid model alias."""
        from .agent_backends.base import _env_with_claude_path, _resolve_claude_bin
        from .agent_backends.claude_code_backend import _normalise_claude_model

        bin_path = _resolve_claude_bin()
        if not bin_path:
            raise CliVisionError("claude binary not found")
        # The `claude` CLI only accepts its own model aliases (sonnet/opus/
        # haiku) or a fully-dated model id — a LiteLLM-style string such as
        # "anthropic/claude-sonnet-4-5" is rejected with "It may not exist or
        # you may not have access to it". Normalise to a CLI alias; an empty
        # result means "omit --model and use the subscription default".
        return bin_path, _env_with_claude_path(bin_path), _normalise_claude_model(self.model)

    def _run_claude_text(self, prompt: str) -> str:
        bin_path, env, cli_model = self._claude_setup()
        argv = [bin_path, "-p", "--tools", ""]
        if cli_model:
            argv += ["--model", cli_model]
        argv.append(prompt)
        rc, out, err = self._exec(argv, env)
        text = (out or "").strip()
        if text:
            return text
        raise CliVisionError(f"claude vision rc={rc}: {(err or '')[:300]}")

    def _run_claude_image(self, b64_data: str, media_type: str, text: str) -> str:
        import json

        bin_path, env, cli_model = self._claude_setup()
        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {"type": "text", "text": text},
                ],
            },
        }
        # stream-json output requires --verbose; --tools "" keeps it a single
        # direct answer instead of an agentic tool-use loop.
        argv = [
            bin_path,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            "",
        ]
        if cli_model:
            argv += ["--model", cli_model]
        rc, out, err = self._exec(argv, env, stdin_text=json.dumps(message) + "\n")

        result_text = ""
        assistant_text: list[str] = []
        for line in (out or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "assistant":
                for blk in evt.get("message", {}).get("content", []) or []:
                    if blk.get("type") == "text" and blk.get("text"):
                        assistant_text.append(blk["text"])
            elif etype == "result" and evt.get("result"):
                result_text = str(evt["result"])
        final = (result_text or "\n".join(assistant_text)).strip()
        if final:
            return final
        raise CliVisionError(f"claude vision rc={rc}: {(err or out or '')[:300]}")

    def _exec(
        self, argv: list[str], env: dict[str, str], stdin_text: str | None = None
    ) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                argv,
                input=stdin_text,
                stdin=None if stdin_text is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                env=env,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            raise CliVisionError(f"{self.backend} vision timed out after {self.timeout}s") from exc
        except FileNotFoundError as exc:
            raise CliVisionError(f"{self.backend} binary not on PATH: {exc}") from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_vision_completer(
    backend: str,
    model: str,
    *,
    has_api_key: bool = False,
) -> VisionCompleter:
    """Pick the right vision transport for *backend*.

    CLI backends (``codex`` / ``claude-code``) use :class:`CliVision` so the
    user's subscription covers verification.  Everything else (``litellm`` and
    unknown ids) uses :class:`LiteLLMVision`.

    ``has_api_key`` is accepted for forward-compat / callers that want to log a
    fallback decision; selection itself is backend-driven.
    """
    b = (backend or "litellm").strip().lower().replace("_", "-")
    if b in ("codex", "claude-code", "claude"):
        return CliVision(backend=b, model=model)
    # litellm, gemini-cli (no verified headless image path yet), and unknown
    # ids all use the LiteLLM transport.
    return LiteLLMVision(model=model)
