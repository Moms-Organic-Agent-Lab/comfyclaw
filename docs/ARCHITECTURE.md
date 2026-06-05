# Architecture

This document maps the abstractions in the paper to concrete modules,
classes, and functions in the source tree. Reviewers and reimplementers
can use it as an index: each entry points to the exact file and the
symbol where the corresponding mechanism lives.

The README has the user-facing tour; this document is for readers who
want to inspect the code while reading the paper.

---

## 1. The harness loop

The agent–generate–verify cycle in the paper is implemented end-to-end
by `ClawHarness`.

| Paper concept | Source | Key symbols |
|---|---|---|
| Top-level loop | `comfyclaw/harness.py` | `ClawHarness.run` |
| Per-run configuration | `comfyclaw/harness.py` | `HarnessConfig` (dataclass) |
| Topology accumulation (`evolve_from_best`) | `comfyclaw/harness.py` | `ClawHarness.run` (start-of-iteration branch on `best_workflow_snapshot`) |
| Iteration accounting and ablation logs | `comfyclaw/harness.py` | `EvolutionEntry`, `EvolutionLog` |
| Per-iteration repair loop (queue + execution errors) | `comfyclaw/harness.py` | `ClawHarness.run` (repair rounds), `_build_repair_feedback` |
| Transient-fault detection (ComfyUI tqdm/BrokenPipe) | `comfyclaw/harness.py` | `_INFRA_ERROR_SIGNALS`, infra-retry branch in `run` |
| Pinned-model invariant (`image_model`) | `comfyclaw/workflow.py` | `WorkflowManager.apply_image_model` |
| Pre-iteration verifier feedback assembly | `comfyclaw/harness.py` | `ClawHarness._build_feedback` |
| Post-generation human feedback capture | `comfyclaw/harness.py` | `ClawHarness._collect_user_feedback_after_generation`, `_feedback_metadata` |
| Post-run skill evolution | `comfyclaw/harness.py`, `comfyclaw/skill_evolver.py` | `ClawHarness._run_skill_evolution`, `SkillEvolver.propose`, `SkillEvolver.apply` |

A single call to `ClawHarness.run(prompt)`:

1. seeds the prompt into every `CLIPTextEncode`-family node connected to a
   sampler positive input (`WorkflowManager.inject_prompt`);
2. calls `ClawAgent.plan_and_patch` to evolve the workflow;
3. submits the workflow to ComfyUI via `ComfyClient.queue_prompt`, with
   up to `max_repair_attempts` recovery rounds for queue/execution errors;
4. fetches the produced image, runs the configured verifier, and feeds the
   score plus critique back to the agent for the next iteration;
5. optionally collects thumbs-up/thumbs-down feedback plus a comment from the
   panel and records it as a good or bad case for later skill evolution;
6. early-stops on the success threshold *or* an explicit user "accept
   now" signal coming through `SyncServer`.

## 2. The agent and its tool catalogue

| Paper concept | Source | Key symbols |
|---|---|---|
| ClawAgent class | `comfyclaw/agent.py` | `ClawAgent`, `ClawAgent.plan_and_patch` |
| System prompt | `comfyclaw/agent.py` | `_SYSTEM_PROMPT_BASE`, `_build_system_prompt` |
| Tool schema (OpenAI / LiteLLM function-calling) | `comfyclaw/agent.py` | `_TOOLS`, `_tool` helper |
| Tool dispatch | `comfyclaw/agent.py` | `ClawAgent._dispatch` |
| Built-in tool implementations | `comfyclaw/agent.py` | `_inspect_workflow`, `_set_param`, `_add_node`, `_connect_nodes`, `_delete_node`, `_add_lora_loader`, `_add_controlnet`, `_add_regional_attention`, `_add_hires_fix`, `_add_inpaint_pass`, `_set_prompt`, `_query_available_models`, `_validate_workflow`, `_finalize_workflow`, `_read_skill`, `_report_evolution_strategy` |
| Topology-mutation primitives | `comfyclaw/workflow.py` | `WorkflowManager.add_node`, `connect`, `set_param`, `delete_node`, `validate`, `clone`, `inject_prompt`, `apply_image_model`, `summarize` |
| Workflow loading (API / UI / prompt-keyed) | `comfyclaw/harness.py` | `_try_sibling_api`, `_ui_to_api`, `ClawHarness.from_workflow_file`, `ClawHarness.from_workflow_dict` |

The agent's full 16-tool surface (as declared in `_TOOLS`):

| Tool | Category | Implementation |
|---|---|---|
| `inspect_workflow` | inspect | `ClawAgent._inspect_workflow` |
| `query_available_models` | inspect | `ClawAgent._query_available_models` |
| `validate_workflow` | validate | `ClawAgent._validate_workflow` |
| `set_param` | edit | `WorkflowManager.set_param` |
| `add_node` | edit | `WorkflowManager.add_node` |
| `connect_nodes` | edit | `WorkflowManager.connect` |
| `delete_node` | edit | `WorkflowManager.delete_node` |
| `set_prompt` | edit (high-level) | `ClawAgent._set_prompt` (auto-routes through samplers → encoders) |
| `add_lora_loader` | topology | `ClawAgent._add_lora_loader` |
| `add_controlnet` | topology | `ClawAgent._add_controlnet` |
| `add_regional_attention` | topology | `ClawAgent._add_regional_attention` |
| `add_hires_fix` | refinement | `ClawAgent._add_hires_fix` |
| `add_inpaint_pass` | refinement | `ClawAgent._add_inpaint_pass` |
| `read_skill` | skills | `ClawAgent._read_skill` (via `SkillsRegistry.read_body`) |
| `report_evolution_strategy` | control | `ClawAgent._report_evolution_strategy` |
| `finalize_workflow` | control | `ClawAgent._finalize_workflow` (auto-validates) |

## 3. Pluggable agent backends

| Paper concept | Source | Key symbols |
|---|---|---|
| Backend protocol | `comfyclaw/agent_backends/base.py` | `AgentBackend`, `ToolCall`, `BackendStatus`, `get_backend`, `probe_all` |
| LiteLLM driver (default; any cloud or local provider) | `comfyclaw/agent_backends/litellm_backend.py` | `LiteLLMBackend` |
| Anthropic `claude` CLI driver | `comfyclaw/agent_backends/claude_code_backend.py` | `ClaudeCodeBackend` |
| OpenAI `codex` CLI driver | `comfyclaw/agent_backends/codex_backend.py` | `CodexBackend` |
| Google `gemini` CLI driver | `comfyclaw/agent_backends/gemini_backend.py` | `GeminiBackend` |
| Shared stdio JSON-envelope session (claude-code / codex / gemini) | `comfyclaw/agent_backends/_stream_session.py` | `StreamSession` |
| Backend selection / fallback | `comfyclaw/agent.py` | `ClawAgent.__init__` (call to `get_backend`) |

CLI backends bypass the host CLI's built-in tools and speak the ComfyClaw
tool schema exclusively. If the requested CLI binary is missing on
`$PATH`, the harness falls back to `LiteLLMBackend` with a warning and the
ComfyUI panel's backend chip turns red (see `agent_backends.json` payload
emitted by `SyncServer`).

## 4. The verifier

| Paper concept | Source | Key symbols |
|---|---|---|
| Region-level VLM critique | `comfyclaw/verifier.py` | `ClawVerifier`, `ClawVerifier.verify` |
| Per-region issue records | `comfyclaw/verifier.py` | `RegionIssue` |
| Requirement-check / detail blend | `comfyclaw/verifier.py` | `VerifierResult`, score formula in `ClawVerifier.verify` (uses `score_weights`) |
| Human-only verifier | `comfyclaw/human_verifier.py` | `HumanVerifier` |
| VLM + human override (co-pilot) | `comfyclaw/human_verifier.py` | `HybridVerifier` |
| Lightweight text completion (used for experience summaries) | `comfyclaw/verifier.py` | `ClawVerifier.complete` |
| Run-mode → verifier selection | `comfyclaw/harness.py` | `ClawHarness.__init__` (`run_mode` → `manual` / `auto` / `copilot`) |

## 5. Skills (Agent Skills spec, progressive disclosure)

The runtime side of the skill library is what ships in this repository.
The *offline* clustering / mutation / held-out-validation orchestration
described in the paper (§3.4) is a separate research pipeline; the
skills it produced are distributed as ordinary `SKILL.md` files, and
this release reuses them through the same import-from-folder /
import-from-`git`-URL flow as any other skill.

| Paper concept | Source | Key symbols |
|---|---|---|
| Registry & discovery roots (`builtin`, `user`, `extra`) | `comfyclaw/skill_manager.py` | `SkillsRegistry`, `SkillsRegistry.__init__`, `_user_skills_root`, `_state_path` |
| `SKILL.md` parser (frontmatter + body) | `comfyclaw/skill_manager.py` | `_parse_skill_md`, `SkillProperties` |
| Enable / disable persistence | `comfyclaw/skill_manager.py` | `SkillsRegistry.set_enabled`, `SkillsRegistry._load_state`, `SkillsRegistry._save_state` |
| Import: folder | `comfyclaw/skill_manager.py` | `SkillsRegistry.import_folder` |
| Import: zip (safe extraction, single top dir) | `comfyclaw/skill_manager.py` | `_safe_unzip_into`, `SkillsRegistry.import_zip` |
| Import: `git clone --depth=1` | `comfyclaw/skill_manager.py` | `_git_clone_into`, `SkillsRegistry.import_git` |
| Read full body on demand (`read_skill` tool) | `comfyclaw/skill_manager.py` | `SkillsRegistry.read_body` |
| Post-run proposal from run evidence | `comfyclaw/skill_evolver.py` | `SkillEvolver`, `SkillEvolutionProposal`, `SkillEvolutionResult` |
| User-skill create / refine write path | `comfyclaw/skill_manager.py` | `SkillsRegistry.upsert_user_skill`, `SkillsRegistry.update_body` |
| Offline cluster / mutate / validate / commit (§3.4) | *not in this release* | The 318 paper-evolved skills are distributed as ordinary `SKILL.md` files; re-running the offline loop is not required to reuse them. |
| Bundled skill catalogue | `comfyclaw/skills/<skill_id>/SKILL.md` | see `comfyclaw/skills/README.md` |

## 6. The ComfyUI plugin (ComfyClaw-Sync)

| Paper concept | Source | Key symbols |
|---|---|---|
| Custom-node entry point | `comfyclaw/custom_node/__init__.py` | `WEB_DIRECTORY` |
| Top-level browser extension | `comfyclaw/custom_node/js/comfy_claw_sync.js` | WebSocket client, tab loader, run controller |
| Tab container | `comfyclaw/custom_node/js/panel/tabs.js` | tab switcher |
| Skills tab (browse / toggle / import / delete) | `comfyclaw/custom_node/js/lib/skills_panel.js` | `SkillsPanel` |
| History tab (gallery + iteration timeline) | `comfyclaw/custom_node/js/lib/history_panel.js` | `HistoryPanel` |
| Iteration scoreboard cards | `comfyclaw/custom_node/js/lib/scoreboard.js` | scoreboard widget |
| Run-mode 3-state toggle (Manual / Auto / Co-pilot) | `comfyclaw/custom_node/js/lib/mode_toggle.js` | mode toggle widget |
| Agent-backend chip | `comfyclaw/custom_node/js/lib/backend_picker.js` | backend picker |
| Toast notifications | `comfyclaw/custom_node/js/lib/toast.js` | toast helper |
| Markdown renderer for skill bodies | `comfyclaw/custom_node/js/lib/markdown.js` | minimal renderer |

## 7. Live-sync (WebSocket) protocol

| Paper concept | Source | Key symbols |
|---|---|---|
| Server | `comfyclaw/sync_server.py` | `SyncServer` |
| Per-tab connection state (workflow, cancel flag, futures, checkpoints) | `comfyclaw/sync_server.py` | `_ConnState` |
| Workflow diff for incremental visualization | `comfyclaw/sync_server.py` | `diff_workflows` |
| Routed broadcasts (per-tab) | `comfyclaw/sync_server.py` | `SyncServer.broadcast` (with `target_ws`), `SyncServer._send_json` |
| Trigger queue (panel → harness) | `comfyclaw/sync_server.py` | `SyncServer.wait_for_trigger` |
| Human-feedback round trip | `comfyclaw/sync_server.py` | `SyncServer.request_feedback`, `SyncServer.wait_for_human_feedback` |
| Skill-evolution approval round trip | `comfyclaw/sync_server.py` | `SyncServer.request_skill_evolution`, `_ConnState.skill_evolution_fut` |
| Per-iteration scoreboard event | `comfyclaw/sync_server.py` | `SyncServer.send_iteration_score` |
| Per-tab checkpoints (save / restore) | `comfyclaw/sync_server.py` | `_ConnState.save_checkpoint`, `restore_checkpoint` |
| Skill-CRUD messages | `comfyclaw/sync_server.py` | handlers in `_handle_message` |

The full inventory of server → client and client → server message types is
documented in the docstring at the top of [comfyclaw/sync_server.py](../comfyclaw/sync_server.py).

## 8. Memory

| Paper concept | Source | Key symbols |
|---|---|---|
| Per-run attempt history (workflow snapshots, score, issues, image) | `comfyclaw/memory.py` | `ClawMemory`, `ClawMemory.record`, `ClawMemory.best`, `ClawMemory.format_history_for_agent` |
| Image cap (bounded memory footprint) | `comfyclaw/memory.py` | `max_images` constructor arg + LRU pruning |
| Experience summary used in next-iteration prompt | `comfyclaw/harness.py` | `ClawHarness._summarize_experience` |

## 9. Companion agents (panel-driven, not in the headline loop)

| Source | Purpose |
|---|---|
| `comfyclaw/chat_agent.py` | In-panel chat-style refinement assistant (lives across iterations of one tab). |
| `comfyclaw/debug_agent.py` | Build-workflow-only path that skips ComfyUI submission entirely. Used by the panel's "Build workflow only" checkbox and by `comfyclaw dry-run`. |

## 10. Command-line interface

| Paper concept | Source | Key symbols |
|---|---|---|
| Entry-point dispatcher | `comfyclaw/cli.py` | `main` |
| `comfyclaw run` (single-shot) | `comfyclaw/cli.py` | `cmd_run` |
| `comfyclaw dry-run` (agent only, no image gen) | `comfyclaw/cli.py` | `cmd_dry_run` |
| `comfyclaw serve` (persistent panel-driven server) | `comfyclaw/cli.py` | `cmd_serve` |
| `comfyclaw install-node` (symlink plugin into ComfyUI) | `comfyclaw/cli.py` | `cmd_install_node` |
| `comfyclaw node-path` (print bundled plugin path) | `comfyclaw/cli.py` | `cmd_node_path` |
| Env-var loading (`.env` auto-load, layered precedence) | `comfyclaw/cli.py` | `_load_dotenv`, `_env_*` helpers |

---

## Reading order suggestion

If you read the paper and want to see how each section materializes in
code, start at the top of this list and walk down — each section
references only the modules above it, so the dependency order matches
the table-of-contents above.

1. `comfyclaw/workflow.py` — the substrate (mutable workflow graph).
2. `comfyclaw/agent_backends/` — the LLM driver protocol.
3. `comfyclaw/agent.py` — the tool catalogue and dispatch loop.
4. `comfyclaw/skill_manager.py` — progressive disclosure of recipes.
5. `comfyclaw/verifier.py` + `comfyclaw/human_verifier.py` — scoring.
6. `comfyclaw/memory.py` — cross-iteration memory.
7. `comfyclaw/sync_server.py` — bidirectional WebSocket bridge.
8. `comfyclaw/harness.py` — orchestration, putting all of the above
   together.
9. `comfyclaw/cli.py` — user-facing wrapper.
10. `comfyclaw/custom_node/` — ComfyUI browser-side plugin.
