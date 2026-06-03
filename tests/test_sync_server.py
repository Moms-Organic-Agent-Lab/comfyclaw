"""
Tests for SyncServer diff computation and per-connection state.

The SyncServer was refactored to per-connection state in v0.2.0 so each
ComfyUI tab gets its own ``_ConnState`` (workflow snapshot, feedback future,
cancel flag).  These tests probe both the pure diff helper and the per-conn
broadcast / messaging machinery without bringing up a real WebSocket.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from comfyclaw.sync_server import SyncServer, _ConnState, diff_workflows


@contextmanager
def _mock_schedule():
    """
    Patch asyncio.run_coroutine_threadsafe so tests can assert *that* a send
    was scheduled without actually running it. We close any coroutine the
    code-under-test produces so Python doesn't emit
    "coroutine was never awaited" warnings at GC time.
    """

    def _capture(coro, _loop):
        # Close the coroutine to release its frame and silence the GC warning.
        try:
            coro.close()
        except Exception:
            pass
        return MagicMock()

    with patch("asyncio.run_coroutine_threadsafe", side_effect=_capture) as mock_run:
        yield mock_run


# ---------------------------------------------------------------------------
# diff_workflows — pure function tests
# ---------------------------------------------------------------------------


class TestDiffWorkflows:
    """Unit tests for the ``diff_workflows`` helper."""

    def test_empty_to_empty(self):
        assert diff_workflows({}, {}) == []

    def test_add_single_node(self):
        old = {}
        new = {
            "1": {"class_type": "KSampler", "inputs": {"seed": 42}},
        }
        ops = diff_workflows(old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "add_node"
        assert ops[0]["id"] == "1"
        assert ops[0]["data"] == new["1"]

    def test_remove_single_node(self):
        old = {
            "1": {"class_type": "KSampler", "inputs": {"seed": 42}},
        }
        ops = diff_workflows(old, {})
        assert len(ops) == 1
        assert ops[0]["op"] == "remove_node"
        assert ops[0]["id"] == "1"

    def test_update_node_inputs(self):
        old = {"1": {"class_type": "KSampler", "inputs": {"seed": 42}}}
        new = {"1": {"class_type": "KSampler", "inputs": {"seed": 99}}}
        ops = diff_workflows(old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "update_node"
        assert ops[0]["id"] == "1"
        assert ops[0]["data"]["inputs"]["seed"] == 99

    def test_no_change(self):
        wf = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd.ckpt"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
        }
        assert diff_workflows(wf, copy.deepcopy(wf)) == []

    def test_mixed_add_remove_update(self):
        old = {
            "1": {"class_type": "A", "inputs": {"x": 1}},
            "2": {"class_type": "B", "inputs": {"y": 2}},
        }
        new = {
            "2": {"class_type": "B", "inputs": {"y": 99}},
            "3": {"class_type": "C", "inputs": {"z": 3}},
        }
        ops = diff_workflows(old, new)
        ops_by_type = {o["op"]: o for o in ops}
        assert "add_node" in ops_by_type
        assert ops_by_type["add_node"]["id"] == "3"
        assert "remove_node" in ops_by_type
        assert ops_by_type["remove_node"]["id"] == "1"
        assert "update_node" in ops_by_type
        assert ops_by_type["update_node"]["id"] == "2"

    def test_ops_sorted_by_node_id(self):
        old = {}
        new = {
            "10": {"class_type": "X", "inputs": {}},
            "2": {"class_type": "Y", "inputs": {}},
            "5": {"class_type": "Z", "inputs": {}},
        }
        ops = diff_workflows(old, new)
        ids = [o["id"] for o in ops]
        assert ids == ["2", "5", "10"]

    def test_add_node_with_link_inputs(self):
        old = {
            "1": {"class_type": "Loader", "inputs": {"ckpt": "model.ckpt"}},
        }
        new = {
            "1": {"class_type": "Loader", "inputs": {"ckpt": "model.ckpt"}},
            "2": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": 42}},
        }
        ops = diff_workflows(old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "add_node"
        assert ops[0]["data"]["inputs"]["model"] == ["1", 0]

    def test_meta_change_triggers_update(self):
        old = {"1": {"class_type": "X", "_meta": {"title": "Old"}, "inputs": {}}}
        new = {"1": {"class_type": "X", "_meta": {"title": "New"}, "inputs": {}}}
        ops = diff_workflows(old, new)
        assert len(ops) == 1
        assert ops[0]["op"] == "update_node"


# ---------------------------------------------------------------------------
# Test helpers — install a fake connection on the server without spinning up
# a real WebSocket. We mock the event loop so ``_send_json`` schedules its
# coroutine on a mock instead of actually running it.
# ---------------------------------------------------------------------------


def _make_server() -> SyncServer:
    return SyncServer(port=0, host="127.0.0.1")


def _attach_running_loop(srv: SyncServer) -> MagicMock:
    """Mark the server as 'running' with a mock loop and thread."""
    srv._loop = MagicMock()
    srv._thread = MagicMock()
    srv._thread.is_alive.return_value = True
    srv._started_ok = True
    return srv._loop


def _register_conn(srv: SyncServer, ws: object | None = None) -> tuple[object, _ConnState]:
    """Insert a fake connection and return its (ws, state) pair."""
    ws = ws or MagicMock(name="ws")
    conn = _ConnState(ws=ws)
    srv._conns[ws] = conn
    return ws, conn


# ---------------------------------------------------------------------------
# SyncServer — broadcast & per-connection workflow state
# ---------------------------------------------------------------------------


class TestSyncServerBroadcast:
    """Tests for SyncServer.broadcast() diff logic and per-connection tracking."""

    def test_first_broadcast_stores_workflow_on_conn(self):
        srv = _make_server()
        _attach_running_loop(srv)
        _, conn = _register_conn(srv)

        wf = {"1": {"class_type": "A", "inputs": {}}}
        with _mock_schedule():
            srv.broadcast(wf)
        assert conn.workflow == wf

    def test_reset_clears_state(self):
        srv = _make_server()
        _, conn = _register_conn(srv)
        conn.workflow = {"1": {"class_type": "A", "inputs": {}}}
        srv.reset()
        assert conn.workflow is None

    def test_reset_empty_sets_to_empty_dict(self):
        """reset(empty=True) sets workflow to {} so the next broadcast diffs."""
        srv = _make_server()
        _, conn = _register_conn(srv)
        conn.workflow = {"1": {"class_type": "A", "inputs": {}}}
        srv.reset(empty=True)
        assert conn.workflow == {}

    def test_broadcast_deepcopies_workflow(self):
        srv = _make_server()
        _attach_running_loop(srv)
        _, conn = _register_conn(srv)

        wf = {"1": {"class_type": "A", "inputs": {"x": [1, 2]}}}
        with _mock_schedule():
            srv.broadcast(wf)
        wf["1"]["inputs"]["x"].append(3)
        assert conn.workflow["1"]["inputs"]["x"] == [1, 2]

    def test_broadcast_sends_full_on_first_call(self):
        """When conn.workflow is None, broadcast schedules a workflow_update send."""
        srv = _make_server()
        _attach_running_loop(srv)
        ws, conn = _register_conn(srv)

        wf = {"1": {"class_type": "A", "inputs": {}}}
        with _mock_schedule() as mock_run:
            srv.broadcast(wf)

        assert mock_run.called
        assert conn.workflow == wf

    def test_broadcast_sends_diff_on_subsequent_call(self):
        """After the first broadcast, subsequent calls produce a workflow_diff."""
        srv = _make_server()
        _attach_running_loop(srv)
        _, conn = _register_conn(srv)

        wf1 = {"1": {"class_type": "A", "inputs": {}}}
        wf2 = {
            "1": {"class_type": "A", "inputs": {}},
            "2": {"class_type": "B", "inputs": {}},
        }

        with _mock_schedule():
            srv.broadcast(wf1)
        assert conn.workflow == wf1

        with _mock_schedule() as mock_run:
            srv.broadcast(wf2)
        assert conn.workflow == wf2
        assert mock_run.called

    def test_broadcast_skips_when_no_changes(self):
        """If workflow hasn't changed since the last broadcast, no send is scheduled."""
        srv = _make_server()
        _attach_running_loop(srv)
        _, conn = _register_conn(srv)

        wf = {"1": {"class_type": "A", "inputs": {}}}
        conn.workflow = copy.deepcopy(wf)

        with _mock_schedule() as mock_run:
            srv.broadcast(copy.deepcopy(wf))
        assert not mock_run.called

    def test_broadcast_noop_when_no_clients(self):
        srv = _make_server()
        _attach_running_loop(srv)

        wf1 = {"1": {"class_type": "A", "inputs": {}}}
        with _mock_schedule() as mock_run:
            srv.broadcast(wf1)
        assert not mock_run.called

    def test_broadcast_targets_only_specified_ws(self):
        """broadcast(target_ws=...) updates only that connection."""
        srv = _make_server()
        _attach_running_loop(srv)
        ws_a, conn_a = _register_conn(srv)
        _, conn_b = _register_conn(srv)

        wf = {"1": {"class_type": "A", "inputs": {}}}
        with _mock_schedule():
            srv.broadcast(wf, target_ws=ws_a)
        assert conn_a.workflow == wf
        assert conn_b.workflow is None


# ---------------------------------------------------------------------------
# Status / completion / error messages
# ---------------------------------------------------------------------------


class TestSyncServerStatusMessages:
    def test_has_clients_empty(self):
        srv = _make_server()
        assert srv.has_clients() is False

    def test_has_clients_with_one(self):
        srv = _make_server()
        _register_conn(srv)
        assert srv.has_clients() is True

    def test_send_status_broadcasts(self):
        srv = _make_server()
        _attach_running_loop(srv)
        _register_conn(srv)

        with _mock_schedule() as mock_run:
            srv.send_status("running", iteration=1, detail="Building workflow")
        assert mock_run.called

    def test_send_complete_broadcasts(self):
        srv = _make_server()
        _attach_running_loop(srv)
        _register_conn(srv)

        with _mock_schedule() as mock_run:
            srv.send_complete(score=0.85, iterations_used=2, image_path="/tmp/out.png")
        assert mock_run.called

    def test_send_error_broadcasts(self):
        srv = _make_server()
        _attach_running_loop(srv)
        _register_conn(srv)

        with _mock_schedule() as mock_run:
            srv.send_error("something went wrong")
        assert mock_run.called

    def test_send_status_noop_no_clients(self):
        srv = _make_server()
        _attach_running_loop(srv)

        with _mock_schedule() as mock_run:
            srv.send_status("running")
        assert not mock_run.called

    def test_send_status_noop_not_running(self):
        """If the server hasn't been started, send_status is a silent no-op."""
        srv = _make_server()
        _register_conn(srv)

        with _mock_schedule() as mock_run:
            srv.send_status("running")
        assert not mock_run.called

    def test_request_feedback_broadcasts(self):
        srv = _make_server()
        _attach_running_loop(srv)
        _register_conn(srv)

        with _mock_schedule() as mock_run:
            srv.request_feedback(
                image_path="/tmp/test.png",
                vlm_summary="score: 0.7",
                iteration=1,
                prompt="a cat",
            )
        assert mock_run.called

    def test_request_feedback_noop_no_clients(self):
        srv = _make_server()
        _attach_running_loop(srv)

        with _mock_schedule() as mock_run:
            srv.request_feedback(image_path="/tmp/test.png")
        assert not mock_run.called

    def test_wait_for_feedback_returns_none_when_not_running(self):
        srv = _make_server()
        result = srv.wait_for_human_feedback(timeout=0.1)
        assert result is None

    def test_wait_for_trigger_returns_none_when_not_running(self):
        srv = _make_server()
        result = srv.wait_for_trigger(timeout=0.1)
        assert result is None


# ---------------------------------------------------------------------------
# _ConnState — checkpoint helpers (pure, no I/O)
# ---------------------------------------------------------------------------


class TestConnStateCheckpoints:
    def test_save_and_list_checkpoints(self):
        conn = _ConnState(ws=MagicMock())
        wf = {"1": {"class_type": "A", "inputs": {"x": 1}}}
        cp_id = conn.save_checkpoint(wf, label="first")
        listing = conn.list_checkpoints()
        assert len(listing) == 1
        assert listing[0]["id"] == cp_id
        assert listing[0]["label"] == "first"

    def test_restore_checkpoint_returns_deep_copy(self):
        conn = _ConnState(ws=MagicMock())
        wf = {"1": {"class_type": "A", "inputs": {"x": [1, 2]}}}
        cp_id = conn.save_checkpoint(wf)
        restored = conn.restore_checkpoint(cp_id)
        assert restored == wf
        # Mutating the restored copy must not affect the stored snapshot.
        restored["1"]["inputs"]["x"].append(3)
        again = conn.restore_checkpoint(cp_id)
        assert again["1"]["inputs"]["x"] == [1, 2]

    def test_restore_missing_returns_none(self):
        conn = _ConnState(ws=MagicMock())
        assert conn.restore_checkpoint("nope") is None


# ---------------------------------------------------------------------------
# Skill evolution approval
# ---------------------------------------------------------------------------


class TestSkillEvolutionApproval:
    async def test_apply_skill_evolution_resolves_pending_future(self):
        import asyncio

        srv = _make_server()
        ws, conn = _register_conn(srv)
        fut = asyncio.get_running_loop().create_future()
        conn.skill_evolution_fut = fut

        await srv._dispatch(ws, conn, {"type": "apply_skill_evolution", "approved": True})

        assert fut.done()
        assert fut.result()["approved"] is True
