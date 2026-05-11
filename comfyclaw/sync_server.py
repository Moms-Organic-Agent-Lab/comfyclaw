"""
SyncServer — lightweight WebSocket server that bridges the Python harness
to connected ComfyUI browser tabs.

Architecture (v2 — per-connection state isolation)
===================================================

One ComfyUI tab  =  one WebSocket connection  =  one _ConnState.

Each _ConnState owns:
  • workflow      — last known API-format workflow dict for this tab
  • checkpoints   — per-tab snapshots
  • cancel        — threading.Event to abort the running generation
  • feedback_fut  — asyncio.Future awaiting human feedback
  • refinement_fut— asyncio.Future awaiting user refinement text

The serve loop (cli.py) calls wait_for_trigger() which returns
``(trigger_dict, source_ws)`` so every subsequent broadcast / status
message can be routed back to exactly the tab that fired the trigger.

Multiple ComfyClaw sessions can share the same workflow (workflowId).
That is handled entirely on the frontend — the backend is session-agnostic.

Message types (server → client):
  workflow_update   — full workflow snapshot (initial / reconnect)
  workflow_diff     — incremental add/remove/update ops
  request_feedback  — ask human for feedback
  generation_status — progress during a run
  generation_complete
  generation_error
  agent_event       — thinking-log entry
  chat_response     — streaming LLM token
  checkpoints_list  — list of checkpoints for this connection
  checkpoint_saved
  checkpoint_restored
  debug_status / debug_result

Message types (client → server):
  hello               — connection registration (connection_id optional)
  trigger_generation
  cancel_generation
  human_feedback
  user_refinement
  chat_message
  debug_workflow
  save_checkpoint
  restore_checkpoint
  list_checkpoints
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

try:
    import websockets
    import websockets.server

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────────────
# Diff helpers
# ──────────────────────────────────────────────────────────────────────────────


def diff_workflows(old: dict, new: dict) -> list[dict]:
    """Compare two API-format workflow dicts and return a list of ops."""
    ops: list[dict] = []
    old_keys, new_keys = set(old), set(new)
    for nid in sorted(new_keys - old_keys, key=lambda k: int(k)):
        ops.append({"op": "add_node", "id": nid, "data": new[nid]})
    for nid in sorted(old_keys - new_keys, key=lambda k: int(k)):
        ops.append({"op": "remove_node", "id": nid})
    for nid in sorted(old_keys & new_keys, key=lambda k: int(k)):
        if old[nid] != new[nid]:
            ops.append({"op": "update_node", "id": nid, "data": new[nid]})
    return ops


_MAX_CHECKPOINTS = 20


# ──────────────────────────────────────────────────────────────────────────────
# Per-connection state
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _ConnState:
    """All mutable state scoped to a single WebSocket connection (= one ComfyUI tab)."""

    ws: Any  # websocket handle
    connection_id: str = ""  # opaque id sent by the client
    workflow: dict | None = None  # last known API-format workflow
    checkpoints: list = field(default_factory=list)
    cp_counter: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)

    # asyncio futures — only valid inside the event-loop thread
    feedback_fut: Any = None  # asyncio.Future[dict] | None
    refinement_fut: Any = None  # asyncio.Future[dict] | None

    # ── Run-mode early-stop (accept_now) ──────────────────────────────────────
    accept_now: threading.Event = field(default_factory=threading.Event)

    # ── checkpoint helpers ────────────────────────────────────────────────────

    def save_checkpoint(self, workflow: dict, label: str = "") -> str:
        self.cp_counter += 1
        cp_id = f"cp_{int(time.time())}_{self.cp_counter}"
        self.checkpoints.append(
            {
                "id": cp_id,
                "label": label or f"Snapshot #{self.cp_counter}",
                "workflow": copy.deepcopy(workflow),
                "timestamp": time.time(),
            }
        )
        if len(self.checkpoints) > _MAX_CHECKPOINTS:
            self.checkpoints = self.checkpoints[-_MAX_CHECKPOINTS:]
        return cp_id

    def list_checkpoints(self) -> list[dict]:
        return [
            {"id": c["id"], "label": c["label"], "timestamp": c["timestamp"]}
            for c in reversed(self.checkpoints)
        ]

    def restore_checkpoint(self, cp_id: str) -> dict | None:
        """Return the workflow dict for *cp_id*, or None if not found."""
        cp = next((c for c in self.checkpoints if c["id"] == cp_id), None)
        return copy.deepcopy(cp["workflow"]) if cp else None


# ──────────────────────────────────────────────────────────────────────────────
# SyncServer
# ──────────────────────────────────────────────────────────────────────────────


class SyncServer:
    """
    Parameters
    ----------
    port            : TCP port to listen on (default 8765).
    host            : Bind address (default ``"0.0.0.0"``).
    model           : Default LiteLLM model for chat / debug agents.
    api_key         : Default LLM API key (falls back to env vars if None).
    server_address  : ComfyUI server address used by the debug agent.
    """

    def __init__(
        self,
        port: int = 8765,
        host: str = "0.0.0.0",
        model: str = "anthropic/claude-sonnet-4-5",
        api_key: str | None = None,
        server_address: str = "127.0.0.1:8188",
        skills_dir: str | None = None,
        quiet: bool = False,
    ) -> None:
        self.port = port
        self.host = host
        self._model = model
        self._api_key = api_key
        self._server_address = server_address
        self._quiet = quiet

        # Skills registry (lazy)
        self._skills_dir = skills_dir
        self._skills_registry: Any = None
        self._skills_lock = threading.Lock()

        # websocket → _ConnState
        self._conns: dict[Any, _ConnState] = {}
        self._conns_lock = threading.Lock()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._started_ok = False

        # Global trigger queue — any tab can trigger, serve loop consumes one at a time.
        # Items: (trigger_dict, source_ws)
        self._trigger_queue: asyncio.Queue | None = None

        # Convenience property kept for legacy code in cli.py that checks
        # ``sync.cancel_requested``; it now delegates to the active connection.
        self._active_ws: Any = None  # set when trigger arrives
        self._active_ws_lock = threading.Lock()

    # ── convenience: active connection's cancel flag ────────────────────────

    @property
    def cancel_requested(self) -> threading.Event:
        with self._active_ws_lock:
            ws = self._active_ws
        if ws:
            with self._conns_lock:
                conn = self._conns.get(ws)
            if conn:
                return conn.cancel
        # Fallback: return a dummy never-set event
        return threading.Event()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not _WS_AVAILABLE:
            log.warning(
                "[SyncServer] 'websockets' not installed — live sync disabled. "
                "Install with: pip install 'comfyclaw[sync]'"
            )
            return
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._started_ok = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="comfyclaw-sync")
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def is_running(self) -> bool:
        return bool(self._started_ok and self._thread and self._thread.is_alive())

    def has_clients(self) -> bool:
        with self._conns_lock:
            return bool(self._conns)

    # ── per-connection workflow broadcast ────────────────────────────────────

    def broadcast(self, workflow: dict, target_ws: Any = None) -> None:
        """
        Send an incremental diff (or full snapshot on first call) to
        *target_ws* only.  If *target_ws* is None, broadcast to all connections.

        Safe to call from any thread.
        """
        targets: list[_ConnState]
        with self._conns_lock:
            if target_ws is not None:
                conn = self._conns.get(target_ws)
                targets = [conn] if conn else []
            else:
                targets = list(self._conns.values())

        if not targets:
            return

        for conn in targets:
            prev = conn.workflow
            if prev is None:
                payload = json.dumps({"type": "workflow_update", "workflow": workflow})
            else:
                ops = diff_workflows(prev, workflow)
                if not ops:
                    conn.workflow = copy.deepcopy(workflow)
                    continue
                payload = json.dumps({"type": "workflow_diff", "ops": ops, "full": workflow})
            conn.workflow = copy.deepcopy(workflow)

            if self._loop:
                asyncio.run_coroutine_threadsafe(self._send_to(conn.ws, payload), self._loop)

    def reset(self, target_ws: Any = None, *, empty: bool = False) -> None:
        """Clear the remembered workflow for *target_ws* (or all connections)."""
        with self._conns_lock:
            conns = (
                [self._conns[target_ws]]
                if target_ws and target_ws in self._conns
                else list(self._conns.values())
            )
        for conn in conns:
            conn.workflow = {} if empty else None

    # ── send helpers ─────────────────────────────────────────────────────────

    def _send_json_to(self, ws: Any, msg: dict) -> None:
        """Send a JSON message to a specific websocket (thread-safe)."""
        if not self._loop or not self.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._send_to(ws, json.dumps(msg)), self._loop)

    def _send_json(self, msg: dict, target_ws: Any = None) -> None:
        """Send to target_ws only, or broadcast to all if target_ws is None."""
        if not self._loop or not self.is_running():
            return
        with self._conns_lock:
            targets = (
                {target_ws} if target_ws and target_ws in self._conns else set(self._conns.keys())
            )
        if not targets:
            return
        payload = json.dumps(msg)
        asyncio.run_coroutine_threadsafe(self._async_broadcast(payload, targets), self._loop)

    # ── generation status — routed to the triggering tab ────────────────────

    def send_status(
        self, state: str, iteration: int = 0, detail: str = "", target_ws: Any = None
    ) -> None:
        self._send_json(
            {
                "type": "generation_status",
                "state": state,
                "iteration": iteration,
                "detail": detail,
            },
            target_ws=target_ws,
        )

    def send_complete(
        self, score: float, iterations_used: int, image_path: str = "", target_ws: Any = None
    ) -> None:
        self._send_json(
            {
                "type": "generation_complete",
                "score": score,
                "iterations_used": iterations_used,
                "image_path": image_path,
            },
            target_ws=target_ws,
        )

    def send_error(self, error: str, target_ws: Any = None) -> None:
        self._send_json({"type": "generation_error", "error": error}, target_ws=target_ws)

    def send_agent_event(
        self,
        event_type: str,
        content: str,
        *,
        iteration: int = 0,
        tool_name: str = "",
        tool_args: dict | None = None,
        target_ws: Any = None,
    ) -> None:
        msg: dict[str, Any] = {
            "type": "agent_event",
            "event_type": event_type,
            "content": content,
            "timestamp": time.time(),
            "iteration": iteration,
        }
        if tool_name:
            msg["tool_name"] = tool_name
        if tool_args:
            msg["tool_args"] = tool_args
        self._send_json(msg, target_ws=target_ws)

    # ── trigger queue (serve loop) ───────────────────────────────────────────

    def wait_for_trigger(self, timeout: float = 0) -> tuple[dict, Any] | None:
        """
        Block until any client sends ``trigger_generation``.

        Returns ``(trigger_dict, source_ws)`` or ``None`` on timeout / not running.
        *timeout=0* means wait forever.
        """
        if not self._loop or not self.is_running():
            return None

        # Run a coroutine that fetches one item from the asyncio Queue
        async def _get() -> tuple[dict, Any]:
            assert self._trigger_queue is not None
            return await self._trigger_queue.get()

        fut = asyncio.run_coroutine_threadsafe(_get(), self._loop)
        try:
            result = fut.result(timeout=timeout if timeout > 0 else None)
            with self._active_ws_lock:
                self._active_ws = result[1]
            return result
        except Exception:
            return None

    # ── human-in-the-loop feedback ───────────────────────────────────────────

    def request_feedback(
        self,
        image_path: str | None = None,
        vlm_summary: str | None = None,
        iteration: int = 0,
        prompt: str = "",
        target_ws: Any = None,
    ) -> None:
        self._send_json(
            {
                "type": "request_feedback",
                "image_path": image_path,
                "vlm_summary": vlm_summary,
                "iteration": iteration,
                "prompt": prompt,
            },
            target_ws=target_ws,
        )

    def wait_for_human_feedback(self, timeout: float = 600.0, source_ws: Any = None) -> dict | None:
        """Block until the client that owns *source_ws* sends human_feedback."""
        if not self._loop or not self.is_running():
            return None
        with self._conns_lock:
            conn = self._conns.get(source_ws) if source_ws else None
            if conn is None and self._conns:
                conn = next(iter(self._conns.values()))
        if conn is None:
            return None

        async def _create() -> asyncio.Future[dict]:
            lp = asyncio.get_running_loop()
            if conn.feedback_fut and not conn.feedback_fut.done():
                conn.feedback_fut.cancel()
            conn.feedback_fut = lp.create_future()
            return conn.feedback_fut

        fut_outer = asyncio.run_coroutine_threadsafe(_create(), self._loop).result(5)
        blocking = asyncio.run_coroutine_threadsafe(self._await_future(fut_outer), self._loop)
        try:
            return blocking.result(timeout=timeout)
        except Exception:
            conn.feedback_fut = None
            return None

    # ── user refinement ──────────────────────────────────────────────────────

    def enable_refinement_listening(self, source_ws: Any = None) -> None:
        if not self._loop or not self.is_running():
            return
        with self._conns_lock:
            conn = (
                self._conns.get(source_ws) if source_ws else next(iter(self._conns.values()), None)
            )
        if conn is None:
            return

        async def _arm() -> None:
            lp = asyncio.get_running_loop()
            if conn.refinement_fut and not conn.refinement_fut.done():
                conn.refinement_fut.cancel()
            conn.refinement_fut = lp.create_future()

        asyncio.run_coroutine_threadsafe(_arm(), self._loop)

    def poll_refinement(self, source_ws: Any = None) -> dict | None:
        with self._conns_lock:
            conn = (
                self._conns.get(source_ws) if source_ws else next(iter(self._conns.values()), None)
            )
        if conn is None:
            return None
        if conn.refinement_fut and conn.refinement_fut.done():
            try:
                return conn.refinement_fut.result()
            except Exception:
                return None
            finally:
                conn.refinement_fut = None
        return None

    # ── legacy checkpoint shims (delegate to active connection) ─────────────

    def save_checkpoint(self, workflow: dict, label: str = "", target_ws: Any = None) -> str:
        with self._conns_lock:
            conn = (
                self._conns.get(target_ws) if target_ws else next(iter(self._conns.values()), None)
            )
        if conn is None:
            return ""
        return conn.save_checkpoint(workflow, label)

    def list_checkpoints(self, target_ws: Any = None) -> list[dict]:
        with self._conns_lock:
            conn = (
                self._conns.get(target_ws) if target_ws else next(iter(self._conns.values()), None)
            )
        return conn.list_checkpoints() if conn else []

    # ── Iteration scoreboard ─────────────────────────────────────────────────

    def send_iteration_score(
        self,
        iteration: int,
        score: float | None,
        delta: float | None,
        critique: str = "",
        image_path: str = "",
        target_ws: Any = None,
    ) -> None:
        """Emit a scoreboard event after each iteration's verifier pass."""
        self._send_json(
            {
                "type": "iteration_score",
                "iteration": iteration,
                "score": score,
                "delta": delta,
                "critique": critique,
                "image_path": image_path,
                "timestamp": time.time(),
            },
            target_ws=target_ws,
        )

    def accept_requested(self, target_ws: Any = None) -> bool:
        """True if the user clicked 'Accept now' on this connection."""
        with self._conns_lock:
            conn = (
                self._conns.get(target_ws) if target_ws else next(iter(self._conns.values()), None)
            )
        return bool(conn and conn.accept_now.is_set())

    # ── Skills registry access ───────────────────────────────────────────────

    def skills_registry(self):
        """Lazily build and return a shared SkillsRegistry."""
        with self._skills_lock:
            if self._skills_registry is None:
                from .skill_manager import SkillsRegistry

                self._skills_registry = SkillsRegistry(self._skills_dir)
            return self._skills_registry

    def reload_skills(self) -> None:
        with self._skills_lock:
            if self._skills_registry is not None:
                self._skills_registry.reload()

    # ── async helpers for chat + debug ───────────────────────────────────────

    async def _send_skills_manifest(self, ws: Any) -> None:
        try:
            manifest = self.skills_registry().get_manifest()
        except Exception as exc:  # noqa: BLE001
            await self._send_skill_error(ws, f"Could not list skills: {exc}")
            return
        await ws.send(json.dumps({"type": "skills_manifest", "skills": manifest}))

    async def _send_skill_error(self, ws: Any, message: str) -> None:
        await ws.send(json.dumps({"type": "skill_error", "error": message}))

    async def _import_skill_folder(self, ws: Any, msg: dict) -> None:
        path = msg.get("path", "")
        try:
            name = self.skills_registry().import_from_folder(path)
            await self._send_skills_manifest(ws)
            await ws.send(
                json.dumps(
                    {
                        "type": "skill_import_result",
                        "ok": True,
                        "action": "folder",
                        "name": name,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            await self._send_skill_error(ws, f"Folder import failed: {exc}")

    async def _import_skill_zip(self, ws: Any, msg: dict) -> None:
        b64 = msg.get("base64", "")
        origin = msg.get("filename", "<uploaded.zip>")
        try:
            name = self.skills_registry().import_from_zip_b64(b64, origin=origin)
            await self._send_skills_manifest(ws)
            await ws.send(
                json.dumps(
                    {
                        "type": "skill_import_result",
                        "ok": True,
                        "action": "zip",
                        "name": name,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            await self._send_skill_error(ws, f"Zip import failed: {exc}")

    async def _import_skill_git(self, ws: Any, msg: dict) -> None:
        url = msg.get("url", "")
        ref = msg.get("ref") or None
        try:
            # git clone is synchronous and short — we run it directly.
            name = self.skills_registry().import_from_git(url, ref)
            await self._send_skills_manifest(ws)
            await ws.send(
                json.dumps(
                    {
                        "type": "skill_import_result",
                        "ok": True,
                        "action": "git",
                        "name": name,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            await self._send_skill_error(ws, f"Git import failed: {exc}")

    async def _handle_chat_message(self, websocket: Any, msg: dict) -> None:
        from .chat_agent import chat_stream

        with self._conns_lock:
            conn = self._conns.get(websocket)
        workflow = copy.deepcopy(conn.workflow) if conn and conn.workflow else None

        message_id: str = msg.get("message_id", "")
        messages: list = msg.get("messages", [])
        model: str = (msg.get("model") or "").strip() or self._model
        api_key: str | None = (msg.get("api_key") or "").strip() or self._api_key
        api_base: str | None = (msg.get("api_base") or "").strip() or None

        try:
            async for token in chat_stream(messages, workflow, model, api_key, api_base):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "chat_response",
                            "message_id": message_id,
                            "token": token,
                            "done": False,
                        }
                    )
                )
        except Exception as exc:
            await websocket.send(
                json.dumps(
                    {
                        "type": "chat_response",
                        "message_id": message_id,
                        "token": f"\n\n⚠️ Error: {exc}",
                        "done": False,
                    }
                )
            )
        finally:
            await websocket.send(
                json.dumps(
                    {
                        "type": "chat_response",
                        "message_id": message_id,
                        "token": "",
                        "done": True,
                    }
                )
            )

    async def _handle_debug_workflow(self, websocket: Any, msg: dict) -> None:
        from .debug_agent import debug_workflow as _debug

        with self._conns_lock:
            conn = self._conns.get(websocket)

        workflow: dict = msg.get("workflow") or {}
        model: str = (msg.get("model") or "").strip() or self._model
        api_key: str | None = (msg.get("api_key") or "").strip() or self._api_key
        api_base: str | None = (msg.get("api_base") or "").strip() or None

        if not workflow and conn and conn.workflow:
            workflow = copy.deepcopy(conn.workflow)

        if not workflow:
            await websocket.send(
                json.dumps(
                    {
                        "type": "debug_result",
                        "summary": "⚠️ No workflow to debug (canvas is empty).",
                        "issues": [],
                        "fixed_workflow": None,
                    }
                )
            )
            return

        await websocket.send(
            json.dumps(
                {
                    "type": "debug_status",
                    "state": "running",
                    "detail": "Analysing workflow…",
                }
            )
        )

        try:
            result = await _debug(workflow, self._server_address, model, api_key, api_base)
        except Exception as exc:
            result = {
                "issues": [],
                "summary": f"⚠️ Debug agent error: {exc}",
                "fixed_workflow": None,
            }

        await websocket.send(
            json.dumps(
                {
                    "type": "debug_result",
                    "summary": result["summary"],
                    "issues": result["issues"],
                    "fixed_workflow": result["fixed_workflow"],
                }
            )
        )

        if result.get("fixed_workflow") and conn:
            conn.workflow = None  # force full snapshot on next broadcast
            self.broadcast(result["fixed_workflow"], target_ws=websocket)

    # ── asyncio internals ────────────────────────────────────────────────────

    @staticmethod
    async def _await_future(fut: asyncio.Future) -> Any:
        return await fut

    async def _send_to(self, ws: Any, payload: str) -> None:
        try:
            await ws.send(payload)
        except Exception as exc:
            log.debug("[SyncServer] Send error: %s", exc)

    async def _async_broadcast(self, payload: str, targets: set[Any]) -> None:
        import websockets.exceptions

        for ws in list(targets):
            try:
                await ws.send(payload)
            except websockets.exceptions.ConnectionClosed:
                with self._conns_lock:
                    self._conns.pop(ws, None)
            except Exception as exc:
                log.debug("[SyncServer] Broadcast error: %s", exc)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # Create the trigger queue inside the event loop
        self._trigger_queue = asyncio.Queue()
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()
            self._loop = None

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        try:
            async with websockets.server.serve(
                self._handler,
                self.host,
                self.port,
                reuse_port=True,
            ):
                log.info("[SyncServer] Listening on ws://%s:%d", self.host, self.port)
                if not self._quiet:
                    print(f"[SyncServer] ✅ Listening on ws://{self.host}:{self.port}")
                self._started_ok = True
                self._ready.set()
                await self._stop_event.wait()
        except Exception as exc:
            log.error("[SyncServer] Failed to start: %s", exc)
            # Errors are always shown.
            print(f"[SyncServer] ❌ Failed to start: {exc}")
            self._ready.set()

    async def _handler(self, websocket: Any) -> None:
        conn = _ConnState(ws=websocket)
        with self._conns_lock:
            self._conns[websocket] = conn
        n = len(self._conns)
        log.debug("[SyncServer] Client connected (%d total)", n)

        # Bootstrap: send this connection's current workflow snapshot
        if conn.workflow is not None:
            try:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "workflow_update",
                            "workflow": conn.workflow,
                        }
                    )
                )
            except Exception:
                pass

        # Also push the skills manifest proactively so the Skills tab is
        # populated even if the user hasn't clicked into it. Without this
        # the panel only fetches on tab-activate and looks empty otherwise.
        try:
            await self._send_skills_manifest(websocket)
        except Exception:
            pass

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        continue
                    await self._dispatch(websocket, conn, msg)
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass
        finally:
            with self._conns_lock:
                self._conns.pop(websocket, None)
            with self._active_ws_lock:
                if self._active_ws is websocket:
                    self._active_ws = None
            log.debug("[SyncServer] Client disconnected (%d remaining)", len(self._conns))

    async def _dispatch(self, ws: Any, conn: _ConnState, msg: dict) -> None:
        """Route a single inbound message to the right handler."""
        t = msg.get("type")

        if t == "hello":
            conn.connection_id = msg.get("connection_id", "")
            log.debug("[SyncServer] hello from connection_id=%r", conn.connection_id)
            # Send current checkpoint list for this connection
            await ws.send(
                json.dumps(
                    {
                        "type": "checkpoints_list",
                        "checkpoints": conn.list_checkpoints(),
                    }
                )
            )

        elif t == "trigger_generation":
            log.info("[SyncServer] trigger_generation from %r", conn.connection_id)
            conn.cancel.clear()
            assert self._trigger_queue is not None
            await self._trigger_queue.put((msg, ws))

        elif t == "cancel_generation":
            log.info("[SyncServer] cancel_generation from %r", conn.connection_id)
            conn.cancel.set()

        elif t == "human_feedback":
            if conn.feedback_fut and not conn.feedback_fut.done():
                conn.feedback_fut.set_result(msg)
                conn.feedback_fut = None

        elif t == "user_refinement":
            if conn.refinement_fut and not conn.refinement_fut.done():
                conn.refinement_fut.set_result(msg)
                conn.refinement_fut = None

        elif t == "chat_message":
            asyncio.ensure_future(self._handle_chat_message(ws, msg))

        elif t == "debug_workflow":
            asyncio.ensure_future(self._handle_debug_workflow(ws, msg))

        elif t == "save_checkpoint":
            workflow = msg.get("workflow") or conn.workflow or {}
            label = msg.get("label", "")
            cp_id = conn.save_checkpoint(workflow, label)
            await ws.send(
                json.dumps(
                    {
                        "type": "checkpoint_saved",
                        "id": cp_id,
                        "label": label or "Snapshot",
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "checkpoints_list",
                        "checkpoints": conn.list_checkpoints(),
                    }
                )
            )

        elif t == "restore_checkpoint":
            cp_id = msg.get("id", "")
            wf = conn.restore_checkpoint(cp_id)
            ok = wf is not None
            await ws.send(
                json.dumps(
                    {
                        "type": "checkpoint_restored",
                        "id": cp_id,
                        "success": ok,
                    }
                )
            )
            if ok:
                conn.workflow = None  # force full snapshot
                self.broadcast(wf, target_ws=ws)

        elif t == "list_checkpoints":
            await ws.send(
                json.dumps(
                    {
                        "type": "checkpoints_list",
                        "checkpoints": conn.list_checkpoints(),
                    }
                )
            )

        elif t == "accept_now":
            log.info("[SyncServer] accept_now from %r", conn.connection_id)
            conn.accept_now.set()

        # ── Skills CRUD ──────────────────────────────────────────────────────
        elif t == "list_skills":
            await self._send_skills_manifest(ws)

        elif t == "read_skill_body":
            try:
                reg = self.skills_registry()
                body = reg.get_body(msg.get("name", ""))
                props = reg.get_properties(msg.get("name", ""))
                await ws.send(
                    json.dumps(
                        {
                            "type": "skill_body",
                            "name": msg.get("name", ""),
                            "body": body,
                            "description": props.description,
                            "location": str(props.location),
                            "license": getattr(props, "license", "") or "",
                        }
                    )
                )
            except KeyError:
                await self._send_skill_error(ws, "Skill not found.")

        elif t == "set_skill_enabled":
            try:
                self.skills_registry().set_enabled(
                    msg.get("name", ""), bool(msg.get("enabled", True))
                )
                await self._send_skills_manifest(ws)
            except Exception as exc:  # noqa: BLE001
                await self._send_skill_error(ws, f"Could not update skill: {exc}")

        elif t == "import_skill_folder":
            asyncio.ensure_future(self._import_skill_folder(ws, msg))

        elif t == "import_skill_zip":
            asyncio.ensure_future(self._import_skill_zip(ws, msg))

        elif t == "import_skill_git":
            asyncio.ensure_future(self._import_skill_git(ws, msg))

        elif t == "delete_skill":
            try:
                self.skills_registry().delete(msg.get("name", ""))
                await self._send_skills_manifest(ws)
                await ws.send(
                    json.dumps(
                        {
                            "type": "skill_import_result",
                            "ok": True,
                            "action": "delete",
                            "name": msg.get("name", ""),
                        }
                    )
                )
            except (KeyError, PermissionError, OSError) as exc:
                await self._send_skill_error(ws, f"Delete failed: {exc}")

        elif t == "reload_skills":
            self.reload_skills()
            await self._send_skills_manifest(ws)

        # ── Agent backend availability probe ─────────────────────────────────
        elif t == "list_agent_backends":
            from .agent_backends import probe_all

            await ws.send(
                json.dumps(
                    {
                        "type": "agent_backends",
                        "backends": [
                            {
                                "name": s.name,
                                "available": s.available,
                                "binary_path": s.binary_path,
                                "detail": s.detail,
                            }
                            for s in probe_all()
                        ],
                    }
                )
            )
