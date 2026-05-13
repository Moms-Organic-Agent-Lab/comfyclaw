/**
 * ComfyClaw Sync Extension  v5.0
 *
 * Connects to the ComfyClaw Python sync server (ws://127.0.0.1:8765 by default)
 * and reloads the ComfyUI canvas in real time whenever the agent modifies the
 * workflow topology.  Also supports human-in-the-loop feedback collection,
 * agent thinking visualization, and user refinement messaging.
 *
 * Protocol — the Python SyncServer sends these message types:
 *
 *   Full snapshot (initial load / reconnect):
 *   { "type": "workflow_update", "workflow": { "<nodeId>": { class_type, inputs, … }, … } }
 *
 *   Incremental diff (subsequent mutations):
 *   { "type": "workflow_diff", "ops": [ {op, id, data?}, … ], "full": {…} }
 *
 *   Feedback request (human-in-the-loop):
 *   { "type": "request_feedback", "image_path": "...", "vlm_summary": "...|null",
 *     "iteration": N, "prompt": "..." }
 *
 *   Agent thinking event:
 *   { "type": "agent_event", "event_type": "strategy|tool_call|thinking|...",
 *     "content": "...", "tool_name": "...", "tool_args": {...}, "iteration": N }
 *
 * Client → server:
 *   { "type": "human_feedback", "text": "...", "score": 0.7, "action": "override"|"accept" }
 *   { "type": "trigger_generation", "prompt": "...", "mode": "...",
 *     "settings": { model, api_key, verifier_model, iterations, verifier_mode } }
 *   { "type": "user_refinement", "text": "..." }
 *
 * Configuration (persisted in localStorage):
 *   comfyclaw_ws_url, comfyclaw_op_delay, comfyclaw-gen-model, comfyclaw-gen-apikey, …
 *
 * Status badge:
 *   🔄 connecting  |  🟢 live  |  ✨ updated (flashes 2 s)  |  🔴 disconnected
 *   📝 awaiting feedback
 */

import { app } from "../../scripts/app.js";

// Modular pieces (Phase 4 redesign).  The legacy code below still owns most
// of the panel, but these modules supply the new tab strip, scoreboard,
// mode toggle, backend picker, skills browser, and history tab.
import { injectStyles } from "./lib/styles.js";
import { createTabStrip }       from "./panel/tabs.js";
import { createModeToggle }     from "./lib/mode_toggle.js";
import { createBackendPicker }  from "./lib/backend_picker.js";
import { createSkillsTab }      from "./lib/skills_panel.js";
import { createHistoryTab }     from "./lib/history_panel.js";
import { buildScoreboardCard }  from "./lib/scoreboard.js";
import { openModal }            from "./lib/modal.js";

injectStyles();

const DEFAULT_WS_URL         = `ws://${window.location.hostname}:8765`;
const RECONNECT_DELAY_MS     = 3000;
const MAX_RECONNECT_ATTEMPTS = 20;
const DEFAULT_OP_DELAY_MS    = 400;

// ── Chat state ────────────────────────────────────────────────────────────────
let _chatHistory      = [];   // [{role, content}]
let _chatMsgIdSeq     = 0;
let _chatStreaming     = false;
let _thinkingChatMsgId = null;  // message_id of the current streaming reply in the log

// ── Generation state ──────────────────────────────────────────────────────────
let _isGenerating = false;
let _genStartTime  = 0;          // epoch ms when last generation started
let _genTimerInterval = null;    // interval for elapsed-time display

// ── Checkpoint state ──────────────────────────────────────────────────────────
let _checkpoints = [];           // [{id, label, timestamp}]

// ── Workflow stats ─────────────────────────────────────────────────────────────
let _nodeCount = 0;              // live count of nodes in the current workflow

// ─────────────────────────────────────────────────────────────────────────────
// Toast notification system
// ─────────────────────────────────────────────────────────────────────────────

let _toastContainer = null;

function _ensureToastContainer() {
  if (_toastContainer) return _toastContainer;
  _toastContainer = document.createElement("div");
  _toastContainer.id = "cc-toast-container";
  _toastContainer.style.cssText = `
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    z-index:99999; display:flex; flex-direction:column; gap:8px;
    align-items:center; pointer-events:none;
  `;
  document.body.appendChild(_toastContainer);
  return _toastContainer;
}

function showToast(msg, type = "info", duration = 2800) {
  const icons = { success:"✓", error:"✕", info:"ℹ", warning:"⚠" };
  const colors = {
    success: { bg:"#a6e3a122", border:"#a6e3a1", text:"#a6e3a1" },
    error:   { bg:"#f38ba822", border:"#f38ba8", text:"#f38ba8" },
    info:    { bg:"#89b4fa22", border:"#89b4fa", text:"#89b4fa" },
    warning: { bg:"#f9e2af22", border:"#f9e2af", text:"#f9e2af" },
  };
  const c = colors[type] || colors.info;
  const container = _ensureToastContainer();
  const el = document.createElement("div");
  el.style.cssText = `
    background:${c.bg}; border:1px solid ${c.border}; color:${c.text};
    padding:8px 16px; border-radius:10px; font-size:12px; font-weight:600;
    pointer-events:none; font-family:system-ui,sans-serif;
    backdrop-filter:blur(8px); box-shadow:0 4px 20px rgba(0,0,0,0.4);
    display:flex; align-items:center; gap:7px;
    opacity:0; transition:opacity 0.2s, transform 0.2s;
    transform:translateY(8px);
  `;
  el.innerHTML = `<span style="font-size:14px;">${icons[type]||icons.info}</span><span>${escHtml(msg)}</span>`;
  container.appendChild(el);
  requestAnimationFrame(() => {
    el.style.opacity = "1";
    el.style.transform = "translateY(0)";
  });
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    setTimeout(() => el.remove(), 220);
  }, duration);
}

const NODE_W = 220;
const NODE_H = 180;
const GAP_X  = 60;
const GAP_Y  = 40;

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function getOpDelay() {
  const stored = localStorage.getItem("comfyclaw_op_delay");
  if (stored !== null) {
    const n = parseInt(stored, 10);
    if (!isNaN(n) && n >= 0) return n;
  }
  return DEFAULT_OP_DELAY_MS;
}

// ─────────────────────────────────────────────────────────────────────────────
// Status badge
// ─────────────────────────────────────────────────────────────────────────────

let statusEl = null;

function createStatusBadge() {
  // Kept for API compatibility (callers update the badge text), but hidden by
  // default: the header connection dot + toasts already surface this info.
  const el = document.createElement("span");
  el.id = "comfyclaw-status";
  el.title = "ComfyClaw Sync — click to reconfigure URL";
  Object.assign(el.style, {
    display: "none",
    position: "fixed",
    bottom: "12px",
    right: "12px",
    zIndex: "9999",
    padding: "4px 10px",
    borderRadius: "12px",
    fontSize: "12px",
    fontFamily: "monospace",
    fontWeight: "bold",
    cursor: "pointer",
    userSelect: "none",
  });
  el.addEventListener("click", promptConfig);
  document.body.appendChild(el);
  return el;
}

const STATUS = {
  connecting:   { bg: "#555",    fg: "#fff", label: "🔄 ComfyClaw: connecting…"    },
  connected:    { bg: "#1a7a3f", fg: "#fff", label: "🟢 ComfyClaw: live"           },
  disconnected: { bg: "#7a1a1a", fg: "#fff", label: "🔴 ComfyClaw: disconnected"   },
  updated:      { bg: "#1a4a7a", fg: "#fff", label: "✨ ComfyClaw: graph updated"  },
  feedback:     { bg: "#7a5a1a", fg: "#fff", label: "📝 ComfyClaw: awaiting feedback" },
};

function setStatus(state, extra) {
  if (!statusEl) return;
  const s = STATUS[state] || STATUS.disconnected;
  statusEl.style.background = s.bg;
  statusEl.style.color = s.fg;
  statusEl.textContent = extra ? `${s.label} — ${extra}` : s.label;
  if (state === "updated") {
    setTimeout(() => setStatus("connected"), 2000);
  }
}

function promptConfig() {
  const current = localStorage.getItem("comfyclaw_ws_url") || DEFAULT_WS_URL;
  const val = window.prompt("ComfyClaw WebSocket URL:", current);
  if (val !== null) {
    localStorage.setItem("comfyclaw_ws_url", val.trim());
    window.location.reload();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Human-in-the-loop feedback panel
// ─────────────────────────────────────────────────────────────────────────────

let _feedbackPanel = null;
let _activeSyncClient = null;

// ── Setup modals (singletons; module-level so the WS handler can reach them)
let _installModal = null;
let _authModal    = null;

function _wsOpen() {
  return _activeSyncClient?.ws?.readyState === WebSocket.OPEN;
}
function _wsSend(payload) {
  if (!_wsOpen()) return false;
  _activeSyncClient.ws.send(JSON.stringify(payload));
  return true;
}

function createFeedbackPanel() {
  const overlay = document.createElement("div");
  overlay.id = "comfyclaw-feedback-overlay";
  Object.assign(overlay.style, {
    display:        "none",
    position:       "fixed",
    top:            "0",
    left:           "0",
    width:          "100vw",
    height:         "100vh",
    background:     "rgba(0,0,0,0.5)",
    zIndex:         "10000",
    justifyContent: "center",
    alignItems:     "center",
  });

  const panel = document.createElement("div");
  Object.assign(panel.style, {
    background:    "#1e1e2e",
    color:         "#cdd6f4",
    borderRadius:  "12px",
    padding:       "24px",
    width:         "520px",
    maxHeight:     "80vh",
    overflowY:     "auto",
    boxShadow:     "0 8px 32px rgba(0,0,0,0.5)",
    fontFamily:    "system-ui, -apple-system, sans-serif",
    fontSize:      "14px",
    lineHeight:    "1.5",
  });

  panel.innerHTML = `
    <h2 style="margin:0 0 8px 0; font-size:18px; color:#cba6f7;">
      📝 ComfyClaw — Your Feedback
    </h2>
    <div id="comfyclaw-fb-meta" style="margin-bottom:12px; color:#a6adc8; font-size:13px;"></div>
    <div id="comfyclaw-fb-vlm" style="margin-bottom:12px; display:none;
         background:#313244; border-radius:8px; padding:12px; font-size:13px;
         white-space:pre-wrap; max-height:200px; overflow-y:auto;"></div>
    <label style="display:block; margin-bottom:4px; font-weight:600; color:#a6adc8;">
      How is the result?
    </label>
    <div id="comfyclaw-fb-scores" style="display:flex; gap:8px; margin-bottom:16px;">
    </div>
    <label style="display:block; margin-bottom:4px; font-weight:600; color:#a6adc8;">
      Feedback (what should be improved?)
    </label>
    <textarea id="comfyclaw-fb-text" rows="4" placeholder="e.g. The lighting is too flat, make it more dramatic. The background needs more depth..."
      style="width:100%; box-sizing:border-box; background:#313244; color:#cdd6f4;
             border:1px solid #45475a; border-radius:8px; padding:10px; font-size:14px;
             font-family:inherit; resize:vertical;"></textarea>
    <div style="display:flex; gap:10px; margin-top:16px; justify-content:flex-end;">
      <button id="comfyclaw-fb-accept" style="padding:8px 20px; border:1px solid #45475a;
              border-radius:8px; background:#313244; color:#a6e3a1; cursor:pointer;
              font-size:14px; font-weight:600;">
        ✓ Accept as-is
      </button>
      <button id="comfyclaw-fb-submit" style="padding:8px 20px; border:none;
              border-radius:8px; background:#cba6f7; color:#1e1e2e; cursor:pointer;
              font-size:14px; font-weight:600;">
        Send Feedback →
      </button>
    </div>
  `;

  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  const scoreButtons = [
    { label: "👍 Good",       score: 0.9, color: "#a6e3a1" },
    { label: "👌 OK",         score: 0.6, color: "#f9e2af" },
    { label: "👎 Needs Work", score: 0.3, color: "#f38ba8" },
  ];
  const scoreContainer = panel.querySelector("#comfyclaw-fb-scores");
  let selectedScore = 0.6;

  scoreButtons.forEach(({ label, score, color }) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.dataset.score = score;
    Object.assign(btn.style, {
      flex:          "1",
      padding:       "8px 4px",
      border:        "2px solid #45475a",
      borderRadius:  "8px",
      background:    "#313244",
      color:         "#cdd6f4",
      cursor:        "pointer",
      fontSize:      "13px",
      fontWeight:    "600",
      transition:    "all 0.15s",
    });
    btn.addEventListener("click", () => {
      selectedScore = score;
      scoreContainer.querySelectorAll("button").forEach(b => {
        b.style.borderColor = "#45475a";
        b.style.background = "#313244";
        b.style.color = "#cdd6f4";
      });
      btn.style.borderColor = color;
      btn.style.background = color + "22";
      btn.style.color = color;
    });
    scoreContainer.appendChild(btn);
  });

  // Pre-select "OK"
  scoreContainer.children[1].click();

  function sendFeedback(action) {
    const text = panel.querySelector("#comfyclaw-fb-text").value.trim();
    const msg = {
      type:   "human_feedback",
      text:   action === "accept" ? "" : text,
      score:  action === "accept" ? 0.85 : selectedScore,
      action: action,
    };
    if (_activeSyncClient && _activeSyncClient.ws && _activeSyncClient.ws.readyState === WebSocket.OPEN) {
      _activeSyncClient.ws.send(JSON.stringify(msg));
      console.log("[ComfyClaw] Sent human_feedback:", msg);
    }
    hideFeedbackPanel();
    setStatus("connected");
  }

  panel.querySelector("#comfyclaw-fb-submit").addEventListener("click", () => sendFeedback("override"));
  panel.querySelector("#comfyclaw-fb-accept").addEventListener("click", () => sendFeedback("accept"));

  return overlay;
}

function showFeedbackPanel(msg) {
  if (!_feedbackPanel) {
    _feedbackPanel = createFeedbackPanel();
  }
  const meta = _feedbackPanel.querySelector("#comfyclaw-fb-meta");
  meta.textContent = `Iteration ${msg.iteration || "?"} — Prompt: "${msg.prompt || "?"}"`;

  const vlmEl = _feedbackPanel.querySelector("#comfyclaw-fb-vlm");
  if (msg.vlm_summary) {
    vlmEl.style.display = "block";
    vlmEl.textContent = "🤖 VLM Assessment:\n" + msg.vlm_summary;
  } else {
    vlmEl.style.display = "none";
  }

  _feedbackPanel.querySelector("#comfyclaw-fb-text").value = "";
  // Re-select "OK" as default
  const scores = _feedbackPanel.querySelector("#comfyclaw-fb-scores");
  if (scores && scores.children[1]) scores.children[1].click();

  _feedbackPanel.style.display = "flex";
  setStatus("feedback");
  // Focus the text area
  setTimeout(() => _feedbackPanel.querySelector("#comfyclaw-fb-text")?.focus(), 100);
}

function hideFeedbackPanel() {
  if (_feedbackPanel) {
    _feedbackPanel.style.display = "none";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// API-format detection & conversion (for full-reload fallback)
// ─────────────────────────────────────────────────────────────────────────────

function isApiFormat(data) {
  if (typeof data !== "object" || data === null || Array.isArray(data)) return false;
  const keys = Object.keys(data);
  if (keys.length === 0) return false;
  return keys.every(k => /^\d+$/.test(k) && data[k] && data[k].class_type);
}

function apiToLitegraph(apiWf) {
  const nodes  = [];
  const links  = [];
  let linkCounter = 0;
  const linkMap   = {};

  const ids  = Object.keys(apiWf).sort((a, b) => parseInt(a) - parseInt(b));
  const COLS = 5;

  const posMap = {};
  ids.forEach((nid, idx) => {
    const col = idx % COLS;
    const row = Math.floor(idx / COLS);
    posMap[nid] = [col * (NODE_W + GAP_X) + 60, row * (NODE_H + GAP_Y) + 60];
  });

  ids.forEach(nid => {
    const apiNode       = apiWf[nid];
    const inputs_meta   = [];
    const widgets_values = [];

    for (const [key, val] of Object.entries(apiNode.inputs || {})) {
      if (Array.isArray(val) && val.length === 2 && typeof val[0] === "string") {
        const [srcId, srcIdx] = val;
        const linkKey = `${srcId}:${srcIdx}`;
        let lid;
        if (linkMap[linkKey] !== undefined) {
          lid = linkMap[linkKey];
        } else {
          lid = linkCounter++;
          linkMap[linkKey] = lid;
          links.push([lid, parseInt(srcId), srcIdx, parseInt(nid), inputs_meta.length, "*"]);
        }
        inputs_meta.push({ name: key, type: "*", link: lid });
      } else {
        widgets_values.push(val);
      }
    }

    nodes.push({
      id:             parseInt(nid),
      type:           apiNode.class_type,
      pos:            posMap[nid],
      size:           [NODE_W, NODE_H],
      flags:          {},
      order:          parseInt(nid),
      mode:           0,
      inputs:         inputs_meta,
      outputs:        [],
      title:          apiNode._meta?.title || apiNode.class_type,
      properties:     { "Node name for S&R": apiNode.class_type },
      widgets_values,
    });
  });

  const maxId = ids.reduce((m, k) => Math.max(m, parseInt(k)), 0);
  return {
    last_node_id:  maxId,
    last_link_id:  linkCounter - 1,
    nodes, links,
    groups:  [],
    config:  {},
    extra:   { comfyclaw: true },
    version: 0.4,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Full workflow loading (used for initial load / reconnect)
// ─────────────────────────────────────────────────────────────────────────────

async function loadWorkflowIntoCanvas(data) {
  try {
    if (isApiFormat(data) && typeof app.loadApiJson === "function") {
      await app.loadApiJson(data);
      console.log("[ComfyClaw] Loaded via app.loadApiJson");
      return true;
    }

    const graphData = isApiFormat(data) ? apiToLitegraph(data) : data;
    if (typeof app.loadGraphData === "function") {
      await app.loadGraphData(graphData);
      console.log("[ComfyClaw] Loaded via app.loadGraphData");
      return true;
    }

    if (app.graph && typeof app.graph.configure === "function") {
      app.graph.configure(isApiFormat(data) ? apiToLitegraph(data) : data);
      app.graph.setDirtyCanvas?.(true, true);
      console.log("[ComfyClaw] Loaded via app.graph.configure");
      return true;
    }

    console.warn("[ComfyClaw] No suitable canvas load method found.");
    return false;
  } catch (err) {
    console.error("[ComfyClaw] Error loading workflow into canvas:", err);
    return false;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Incremental diff application — node-by-node canvas updates
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Accumulated API-format workflow the client knows about.
 * Updated on every op so we can reload the full graph at each step.
 */
let _currentApiWorkflow = {};

/**
 * Temporarily highlight a node using LiteGraph's native color system.
 */
function highlightNode(nodeId, durationMs = 1500) {
  const lgNode = app.graph?.getNodeById(parseInt(nodeId));
  if (!lgNode) return;

  const origColor   = lgNode.color;
  const origBgcolor = lgNode.bgcolor;

  lgNode.color   = "#4a9eff";
  lgNode.bgcolor = "#1a3a5a";
  app.graph?.setDirtyCanvas?.(true, true);

  setTimeout(() => {
    lgNode.color   = origColor;
    lgNode.bgcolor = origBgcolor;
    app.graph?.setDirtyCanvas?.(true, true);
  }, durationMs);
}

/**
 * Apply a single diff op:
 *  1. Update ``_currentApiWorkflow`` (the accumulated state).
 *  2. Reload the full graph via ComfyUI's native loader (handles layout).
 *  3. Highlight the affected node so the user can see what changed.
 */
async function applyOp(op) {
  switch (op.op) {
    case "add_node":
      _currentApiWorkflow[op.id] = op.data;
      await loadWorkflowIntoCanvas(_currentApiWorkflow);
      highlightNode(op.id);
      console.log(`[ComfyClaw] +node ${op.id} (${op.data.class_type})`);
      break;

    case "remove_node":
      delete _currentApiWorkflow[op.id];
      await loadWorkflowIntoCanvas(_currentApiWorkflow);
      console.log(`[ComfyClaw] -node ${op.id}`);
      break;

    case "update_node":
      _currentApiWorkflow[op.id] = op.data;
      await loadWorkflowIntoCanvas(_currentApiWorkflow);
      highlightNode(op.id, 800);
      console.log(`[ComfyClaw] ~node ${op.id} (updated)`);
      break;

    default:
      console.warn(`[ComfyClaw] Unknown op: ${op.op}`);
  }
}

/**
 * Process an array of diff ops sequentially with a delay between each op
 * for a smooth visual build-up effect.
 */
async function applyDiffOps(ops) {
  const delayMs = getOpDelay();
  for (let i = 0; i < ops.length; i++) {
    await applyOp(ops[i]);
    if (delayMs > 0 && i < ops.length - 1) {
      await sleep(delayMs);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// LLM Provider registry
// ─────────────────────────────────────────────────────────────────────────────

const PROVIDERS = {
  anthropic: {
    label: "Anthropic", emoji: "◆", color: "#cd7f32",
    models: [
      { value: "anthropic/claude-sonnet-4-5",        label: "Claude Sonnet 4.5" },
      { value: "anthropic/claude-sonnet-4-20250514",  label: "Claude Sonnet 4"   },
      { value: "anthropic/claude-opus-4-20250514",    label: "Claude Opus 4"     },
      { value: "anthropic/claude-haiku-3-5-20241022", label: "Claude Haiku 3.5"  },
    ],
  },
  openai: {
    label: "OpenAI", emoji: "○", color: "#10a37f",
    models: [
      { value: "openai/gpt-4o",      label: "GPT-4o"     },
      { value: "openai/gpt-4o-mini", label: "GPT-4o mini" },
      { value: "openai/o3",          label: "o3"          },
      { value: "openai/o4-mini",     label: "o4-mini"     },
    ],
  },
  google: {
    label: "Google", emoji: "✦", color: "#4285f4",
    models: [
      { value: "gemini/gemini-2.5-pro",  label: "Gemini 2.5 Pro"   },
      { value: "gemini/gemini-2.5-flash", label: "Gemini 2.5 Flash" },
      { value: "gemini/gemini-2.0-flash", label: "Gemini 2.0 Flash" },
    ],
  },
  groq: {
    label: "Groq", emoji: "⚡", color: "#f97316",
    models: [
      { value: "groq/llama-3.3-70b-versatile",       label: "Llama 3.3 70B"    },
      { value: "groq/deepseek-r1-distill-llama-70b",  label: "DeepSeek R1 70B"  },
      { value: "groq/mixtral-8x7b-32768",             label: "Mixtral 8×7B"     },
    ],
  },
  ollama: {
    label: "Ollama", emoji: "▲", color: "#a6e3a1",
    models: [
      { value: "ollama/llama3.2",    label: "Llama 3.2"    },
      { value: "ollama/qwen2.5:14b", label: "Qwen 2.5 14B" },
      { value: "ollama/mistral:7b",  label: "Mistral 7B"   },
    ],
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Workflow identity helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Derive a short stable identity string for the current ComfyUI workflow.
 *
 * Priority (best → fallback):
 *  1. ComfyUI Desktop workflowManager active tab name
 *  2. app.graph.extra?.info?.name  (saved by ComfyUI when you "Save As")
 *  3. document.title  (newer ComfyUI sets "<name> - ComfyUI")
 *  4. A quick 8-char content hash of the top-level node class_type set
 *     (changes when the node set changes, stable for minor input edits)
 *  5. "workflow" as the final fallback
 */
// Safely coerce a possibly-reactive value (Vue Ref, computed, etc.) to a
// plain string. Returns "" for anything that isn't a non-empty string after
// unwrapping. Without this, ComfyUI's reactive `activeWorkflow.name` (a Ref
// object) leaks into session.name and renders as "[object Object]".
function _asStr(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "object") {
    // Vue Ref / computed: { value: "..." }
    if (typeof v.value === "string") return v.value;
    // Generic objects coerce to "[object Object]" — refuse them.
    return "";
  }
  return String(v);
}

function _detectWorkflowIdentity(apiWorkflow = null) {
  // 1. ComfyUI Desktop workflow manager
  try {
    const wm = app.ui?.workflowManager || app.workflowManager;
    const active = wm?.activeWorkflow || wm?.currentWorkflow;
    const n = _asStr(active?.name);
    if (n) return { name: n, source: "wm" };
  } catch(_) {}

  // 2. graph.extra.info.name  (written on "Save As")
  try {
    const n = _asStr(app.graph?.extra?.info?.name) || _asStr(app.graph?.extra?.name);
    if (n && n.trim() && n !== "unnamed") return { name: n.trim(), source: "graph" };
  } catch(_) {}

  // 3. document.title  ("My Workflow - ComfyUI")
  try {
    const title = document.title.replace(/\s*[-–|]\s*ComfyUI.*$/i, "").trim();
    if (title && title.toLowerCase() !== "comfyui" && title.length > 1)
      return { name: title, source: "title" };
  } catch(_) {}

  // 4. Content hash — top N node class_types sorted → short djb2 hash
  const wf = apiWorkflow || _currentApiWorkflow;
  if (wf && Object.keys(wf).length > 0) {
    const types = Object.values(wf)
      .map(n => n.class_type || "?")
      .sort()
      .join(",")
      .slice(0, 400);
    let h = 5381;
    for (let i = 0; i < types.length; i++) h = ((h << 5) + h) ^ types.charCodeAt(i);
    const hash = (h >>> 0).toString(16).slice(0, 8);
    return { name: `workflow-${hash}`, source: "hash" };
  }

  return { name: "workflow", source: "fallback" };
}

/**
 * Return true if two workflow identities are "the same".
 * Hash-based IDs are compared exactly; name-based IDs by case-insensitive string.
 */
function _isSameWorkflow(idA, idB) {
  if (!idA || !idB) return false;
  return idA.toLowerCase() === idB.toLowerCase();
}

// ─────────────────────────────────────────────────────────────────────────────
// Session management
// ─────────────────────────────────────────────────────────────────────────────

const _SESSION_KEY        = "comfyclaw_sessions_v3";   // bumped to avoid stale data
const _ACTIVE_SESSION_KEY = "comfyclaw_active_session_v3";

function _mkSession(name = "Session 1", workflowId = "") {
  return {
    id:         `s${Date.now()}${Math.random().toString(36).slice(2,6)}`,
    name:       _asStr(name) || "Session 1",
    workflowId: _asStr(workflowId),  // identity string from _detectWorkflowIdentity().name
    prompt:     "",
    chatHistory:[],
    provider:   "anthropic",
    model:      "",
    createdAt:  Date.now(),
  };
}

let _sessions = (() => { try { return JSON.parse(localStorage.getItem(_SESSION_KEY)) || []; } catch(_){} return []; })();
// Migrate old sessions: ensure workflowId exists and that .name / .workflowId
// are plain strings. Older builds wrote Vue Refs in here, which JSON.stringify
// turned into {} and then rendered as "[object Object]" on every reload.
_sessions.forEach((s, i) => {
  if (s.workflowId === undefined) s.workflowId = "";
  if (typeof s.name !== "string" || !s.name) s.name = `Session ${i + 1}`;
  if (typeof s.workflowId !== "string") s.workflowId = "";
});
if (!_sessions.length) _sessions = [_mkSession()];
// Persist sanitized sessions back so the broken titles never come back.
try { localStorage.setItem(_SESSION_KEY, JSON.stringify(_sessions)); } catch (_) {}

let _activeSessionId = localStorage.getItem(_ACTIVE_SESSION_KEY) || _sessions[0].id;
if (!_sessions.find(s => s.id === _activeSessionId)) _activeSessionId = _sessions[0].id;

function _activeSession() { return _sessions.find(s => s.id === _activeSessionId) || _sessions[0]; }

function _persistSessions() { localStorage.setItem(_SESSION_KEY, JSON.stringify(_sessions)); }

function _captureCurrentSession() {
  const sess = _activeSession();
  const promptEl = document.getElementById("comfyclaw-gen-prompt");
  const modelEl  = document.getElementById("comfyclaw-gen-model");
  const provEl   = document.getElementById("comfyclaw-provider-state");
  if (promptEl) sess.prompt = promptEl.value;
  sess.chatHistory = [..._chatHistory];
  if (modelEl)  sess.model    = modelEl.value;
  if (provEl)   sess.provider = provEl.dataset.provider || "anthropic";
  // Always keep the workflow identity fresh
  if (!sess.workflowId) {
    const { name } = _detectWorkflowIdentity();
    sess.workflowId = name;
  }
  _persistSessions();
}

function _applySession(sessionId) {
  const sess = _sessions.find(s => s.id === sessionId);
  if (!sess) return;
  const promptEl = document.getElementById("comfyclaw-gen-prompt");
  if (promptEl) promptEl.value = sess.prompt || "";
  _chatHistory = [...(sess.chatHistory || [])];
  _setActiveProvider(sess.provider || "anthropic", false);
  const modelEl = document.getElementById("comfyclaw-gen-model");
  if (modelEl && sess.model) modelEl.value = sess.model;
  // Rebuild chat log
  clearAgentLog();
  for (const msg of _chatHistory) {
    appendAgentLog({ event_type: msg.role === "user" ? "user" : "assistant_done",
                     content: msg.content, timestamp: Date.now() / 1000 });
  }
}

function _switchSession(id) {
  if (id === _activeSessionId) return;
  _captureCurrentSession();
  _activeSessionId = id;
  localStorage.setItem(_ACTIVE_SESSION_KEY, id);
  _applySession(id);
  _renderSessionTabs();
}

function _newSession(nameHint = "") {
  _captureCurrentSession();
  const { name: wfId } = _detectWorkflowIdentity();
  // Count existing sessions for this workflow to number the new one
  const existing = _sessions.filter(s => _isSameWorkflow(s.workflowId || "", wfId)).length;
  const phaseNames = ["Early draft", "Refinements", "New features", "Debug", "Experiments"];
  const phaseName  = phaseNames[existing] || `Phase ${existing + 1}`;
  const defaultName = nameHint || (
    wfId && wfId !== "workflow"
      ? `${wfId.slice(0, 14)} · ${phaseName}`
      : `Session ${_sessions.length + 1}`
  );
  const sess = _mkSession(defaultName, wfId);
  _sessions.push(sess);
  _persistSessions();
  _activeSessionId = sess.id;
  localStorage.setItem(_ACTIVE_SESSION_KEY, sess.id);
  _chatHistory = [];
  const promptEl = document.getElementById("comfyclaw-gen-prompt");
  if (promptEl) promptEl.value = "";
  clearAgentLog();
  _setActiveProvider("anthropic", false);
  _renderSessionTabs();
}

/**
 * Return all sessions that belong to the same workflow as *wfId*.
 * Used for the "sibling sessions" tooltip / context.
 */
function _sessionsForWorkflow(wfId) {
  if (!wfId || wfId === "workflow") return [];
  return _sessions.filter(s => _isSameWorkflow(s.workflowId || "", wfId));
}

function _deleteSession(id) {
  if (_sessions.length <= 1) return;
  _sessions = _sessions.filter(s => s.id !== id);
  _persistSessions();
  if (_activeSessionId === id) {
    _activeSessionId = _sessions[0].id;
    localStorage.setItem(_ACTIVE_SESSION_KEY, _activeSessionId);
    _applySession(_activeSessionId);
  }
  _renderSessionTabs();
}

function _renderSessionTabs() {
  const bar = document.getElementById("comfyclaw-sessions-tabs");
  if (!bar) return;
  bar.innerHTML = "";

  const { name: currentWfId } = _detectWorkflowIdentity();

  _sessions.forEach((sess, idx) => {
    // Defensive: ensure name is always a string at render time.
    if (typeof sess.name !== "string" || !sess.name) sess.name = `Session ${idx + 1}`;
    const isActive  = sess.id === _activeSessionId;
    // Mismatch: session was linked to a different workflow than current canvas
    const hasMismatch = sess.workflowId
      && !sess.workflowId.startsWith("workflow-")   // not a hash (hash changes often)
      && !_isSameWorkflow(sess.workflowId, currentWfId);

    const tab = document.createElement("div");
    tab.dataset.sessionId = sess.id;
    tab.title = sess.workflowId
      ? `${sess.name}\nLinked to: ${sess.workflowId}`
      : sess.name;
    tab.style.cssText = `
      display:flex; align-items:center; gap:3px; padding:3px 9px;
      border-radius:6px; cursor:pointer; font-size:11px; font-weight:600;
      white-space:nowrap; max-width:120px; flex-shrink:0; transition:all 0.15s;
      background:${isActive ? "#45475a" : "transparent"};
      color:${isActive ? "#cdd6f4" : "#585b70"};
      ${hasMismatch ? "border:1px dashed #f9e2af44;" : "border:1px solid transparent;"}
    `;

    const lbl = document.createElement("span");
    lbl.textContent = sess.name;
    lbl.style.cssText = "overflow:hidden; text-overflow:ellipsis; flex:1;";
    // Double-click to rename
    lbl.addEventListener("dblclick", e => {
      e.stopPropagation();
      const inp = document.createElement("input");
      inp.value = sess.name;
      inp.style.cssText = "background:transparent;color:inherit;border:none;outline:none;font:inherit;width:80px;";
      lbl.replaceWith(inp);
      inp.focus(); inp.select();
      const commit = () => { sess.name = inp.value.trim() || sess.name; _persistSessions(); _renderSessionTabs(); };
      inp.addEventListener("blur", commit);
      inp.addEventListener("keydown", ev => {
        if (ev.key === "Enter") { ev.preventDefault(); commit(); }
        if (ev.key === "Escape") _renderSessionTabs();
      });
    });
    tab.appendChild(lbl);

    // Mismatch warning dot
    if (hasMismatch) {
      const warn = document.createElement("span");
      warn.textContent = "⚠";
      warn.title = `This session was created for: ${sess.workflowId}\nCurrent canvas: ${currentWfId}`;
      warn.style.cssText = "font-size:9px;color:#f9e2af;flex-shrink:0;";
      tab.appendChild(warn);
    }

    if (_sessions.length > 1) {
      const x = document.createElement("span");
      x.textContent = "×";
      x.style.cssText = "font-size:14px;opacity:0.4;flex-shrink:0;line-height:1;margin-left:2px;";
      x.title = "Close session";
      x.addEventListener("click", e => { e.stopPropagation(); _deleteSession(sess.id); });
      tab.appendChild(x);
    }
    tab.addEventListener("click", () => _switchSession(sess.id));
    bar.appendChild(tab);
  });

  // Update the workflow context bar below the tabs
  _updateWorkflowContextBar(currentWfId);

  // Scroll active tab into view
  const active = bar.querySelector(`[data-session-id="${_activeSessionId}"]`);
  active?.scrollIntoView?.({ block:"nearest", inline:"nearest" });
}

/**
 * Best-effort switch of ComfyUI's active workflow tab to one matching `name`.
 * Returns true on success. Handles both the modern Pinia store
 * (app.extensionManager.workflow) and the legacy app.workflowManager.
 */
async function switchToWorkflowByName(name) {
  const target = String(name ?? "").replace(/\.json$/i, "").toLowerCase();
  if (!target) return false;
  const _unwrap = (v) => (v && typeof v === "object" && "value" in v) ? v.value : v;
  const norm = (s) => String(_unwrap(s) ?? "").replace(/\.json$/i, "").toLowerCase();
  const matches = (w) => {
    if (!w) return false;
    const cand = [w.filename, w.key, w.path, w.name].map(_unwrap);
    return cand.some((c) => c && (norm(c) === target || norm(c).endsWith(target) || target.endsWith(norm(c))));
  };
  try {
    const ws = app?.extensionManager?.workflow;
    if (ws) {
      const open = (ws.openWorkflows || []).find(matches);
      if (open) { await ws.openWorkflow(open); return true; }
      const saved = (ws.workflows || []).find(matches);
      if (saved) { await ws.openWorkflow(saved); return true; }
    }
    const wm = app?.workflowManager;
    if (wm) {
      const hit = (wm.workflows || wm.openWorkflows || []).find(matches);
      if (hit) {
        if (typeof wm.openWorkflow === "function") await wm.openWorkflow(hit);
        else if (typeof wm.setWorkflow === "function") await wm.setWorkflow(hit);
        else if (typeof wm.setActiveWorkflow === "function") await wm.setActiveWorkflow(hit);
        return true;
      }
    }
  } catch (err) {
    console.warn("[ComfyClaw] switchToWorkflowByName failed:", err);
  }
  return false;
}

/**
 * Update (or create) a thin bar below the session tabs showing which
 * workflow the active session is bound to, plus actions: open that
 * workflow in the canvas, re-link the session to the current canvas,
 * or start a new session.
 *
 * The bar lives at the top of the Generate slot so it's visible even
 * when the controls section is collapsed (the default).
 */
function _updateWorkflowContextBar(currentWfId) {
  let bar = document.getElementById("cc-wf-context-bar");
  if (!bar) {
    // Preferred home: top of the Generate slot (sibling of #comfyclaw-gen-body).
    // Falls back to inside the controls body if the slot hasn't been built yet.
    const genBody = document.getElementById("comfyclaw-gen-body");
    const slot    = genBody?.parentElement;
    if (!genBody) return;
    bar = document.createElement("div");
    bar.id = "cc-wf-context-bar";
    bar.style.cssText = `
      display:none; align-items:center; gap:6px;
      padding:5px 10px; flex-shrink:0;
      background:var(--cc-surface-tint);
      border-bottom:1px solid var(--cc-border);
      font-size:11px; line-height:1.2;
    `;
    if (slot && slot.contains(genBody)) slot.insertBefore(bar, genBody);
    else genBody.insertBefore(bar, genBody.firstChild);
  } else {
    // Re-home the bar if augmentation moved gen-body into a new slot after
    // the bar was first created in the controls body.
    const genBody = document.getElementById("comfyclaw-gen-body");
    const slot    = genBody?.parentElement;
    if (slot && bar.parentElement !== slot) slot.insertBefore(bar, genBody);
  }

  const sess = _activeSession();
  const sessWf = sess?.workflowId || "";
  const mismatch = sessWf && !sessWf.startsWith("workflow-")
                 && !_isSameWorkflow(sessWf, currentWfId);

  if (!sessWf && currentWfId === "workflow") {
    bar.style.display = "none";
    return;
  }

  bar.style.display = "flex";

  if (mismatch) {
    bar.innerHTML = `
      <span style="color:var(--cc-accent-yellow);flex-shrink:0;">⚠</span>
      <span style="flex:1;color:var(--cc-fg-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
            title="Session linked to: ${escHtml(sessWf)}\nCurrent canvas: ${escHtml(currentWfId)}">
        Session linked to <strong style="color:var(--cc-accent-yellow);">${escHtml(sessWf)}</strong>
      </span>
      <button id="cc-wf-open-btn" title="Switch the canvas to the workflow this session is bound to"
              style="padding:2px 8px;border:1px solid var(--cc-accent-green);border-radius:5px;
                     background:transparent;color:var(--cc-accent-green);cursor:pointer;font-size:10px;
                     font-weight:600;flex-shrink:0;white-space:nowrap;">
        Open ${escHtml(sessWf.length > 16 ? sessWf.slice(0,16)+"…" : sessWf)}
      </button>
      <button id="cc-wf-link-btn" title="Re-link this session to the current canvas"
              style="padding:2px 8px;border:1px solid var(--cc-border);border-radius:5px;
                     background:transparent;color:var(--cc-fg-muted);cursor:pointer;font-size:10px;
                     font-weight:600;flex-shrink:0;white-space:nowrap;">
        Relink
      </button>
      <button id="cc-wf-newsess-btn" title="Start a new session for the current canvas"
              style="padding:2px 6px;border:none;background:transparent;
                     color:var(--cc-fg-dim);cursor:pointer;font-size:10px;flex-shrink:0;">
        + New
      </button>
    `;
    document.getElementById("cc-wf-open-btn")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const origText = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Opening…";
      let ok = false;
      try {
        ok = await switchToWorkflowByName(sessWf);
      } catch (err) {
        console.warn("[ComfyClaw] open workflow failed:", err);
      }
      // Always restore the button — the bar might or might not get rebuilt below.
      btn.disabled = false;
      btn.textContent = origText;
      if (ok) {
        showToast(`Switched canvas to "${sessWf}"`, "success");
        // The local tab switch doesn't trigger a server workflow_update, so
        // we have to re-render the bar ourselves. RAF gives Pinia one tick
        // to propagate activeWorkflow before _detectWorkflowIdentity reads it.
        requestAnimationFrame(() => _renderSessionTabs());
      } else {
        showToast(`Couldn't find "${sessWf}" — open it from the workflow menu`, "warning", 4000);
      }
    });
    document.getElementById("cc-wf-link-btn")?.addEventListener("click", () => {
      if (sess) { sess.workflowId = currentWfId; _persistSessions(); }
      _renderSessionTabs();
      showToast(`Session relinked to "${currentWfId}"`, "success");
    });
    document.getElementById("cc-wf-newsess-btn")?.addEventListener("click", () => {
      _newSession();
    });
  } else {
    // Matched (or unbound) — show a subtle context line + sibling session count.
    const displayId  = sessWf || currentWfId;
    const siblings   = _sessionsForWorkflow(displayId).filter(s => s.id !== sess?.id);
    const siblingTip = siblings.length
      ? `${siblings.length} other session${siblings.length > 1 ? "s" : ""} on this workflow: ${siblings.map(s => s.name).join(", ")}`
      : "Only session on this workflow";
    const siblingBadge = siblings.length
      ? `<span title="${escHtml(siblingTip)}" style="
           background:var(--cc-surface-2);border:1px solid var(--cc-border);border-radius:4px;
           padding:1px 5px;font-size:9px;color:var(--cc-fg-dim);cursor:default;flex-shrink:0;">
           +${siblings.length} session${siblings.length > 1 ? "s" : ""}
         </span>`
      : "";
    bar.innerHTML = `
      <span style="color:var(--cc-fg-dim); font-size:10px;flex-shrink:0;">🗂</span>
      <span style="flex:1;color:var(--cc-fg-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
            title="Canvas: ${escHtml(currentWfId)}">
        ${escHtml(displayId.length > 28 ? displayId.slice(0,28)+"…" : displayId)}
      </span>
      ${siblingBadge}
      <button id="cc-wf-relink-btn" title="Re-link to a different workflow name"
              style="padding:2px 6px;border:none;background:transparent;
                     color:var(--cc-fg-dim);cursor:pointer;font-size:10px;flex-shrink:0;">✎</button>
    `;
    document.getElementById("cc-wf-relink-btn")?.addEventListener("click", () => {
      const name = prompt("Set workflow label for this session:", sessWf || currentWfId);
      if (name !== null && sess) {
        sess.workflowId = _asStr(name).trim() || currentWfId;
        _persistSessions();
        _renderSessionTabs();
      }
    });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Provider settings (api key + base url, stored per-provider in localStorage)
// ─────────────────────────────────────────────────────────────────────────────

const _PROV_SETTINGS_KEY = "comfyclaw_provider_cfg";

let _providerSettings = (() => {
  try { return JSON.parse(localStorage.getItem(_PROV_SETTINGS_KEY)) || {}; }
  catch(_) { return {}; }
})();

function _saveProv() { localStorage.setItem(_PROV_SETTINGS_KEY, JSON.stringify(_providerSettings)); }
function _getPS(pKey, field) { return _providerSettings[pKey]?.[field] || ""; }
function _setPS(pKey, field, val) {
  if (!_providerSettings[pKey]) _providerSettings[pKey] = {};
  _providerSettings[pKey][field] = val;
  _saveProv();
  _refreshProvDots();
}

/** Return {apiKey, baseUrl} to include in any outgoing WS message. */
function _activeProvPayload() {
  const stateEl = document.getElementById("comfyclaw-provider-state");
  const key = stateEl?.dataset.provider || "anthropic";
  return {
    api_key:  _getPS(key, "apiKey")  || undefined,
    api_base: _getPS(key, "baseUrl") || undefined,
  };
}

function _refreshProvDots() {
  document.querySelectorAll(".cc-provider-btn").forEach(btn => {
    const hasKey = !!_getPS(btn.dataset.key, "apiKey");
    let dot = btn.querySelector(".cc-pdot");
    if (hasKey && !dot) {
      dot = Object.assign(document.createElement("span"), { className: "cc-pdot" });
      dot.style.cssText = "width:5px;height:5px;border-radius:50%;background:#a6e3a1;flex-shrink:0;";
      btn.appendChild(dot);
    } else if (!hasKey && dot) { dot.remove(); }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings modal
// ─────────────────────────────────────────────────────────────────────────────

function escHtml(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }
function escAttr(s) { return escHtml(s).replace(/"/g, "&quot;"); }

function _settingsInputStyle(extra = "") {
  return `width:100%;box-sizing:border-box;background:#181825;color:#cdd6f4;
          border:1px solid #45475a;border-radius:8px;padding:7px 11px;
          font-size:12px;outline:none;transition:border-color 0.15s;${extra}`;
}
function _settingsLabelStyle() {
  return `display:block;font-size:11px;font-weight:600;color:#a6adc8;
          margin-bottom:4px;letter-spacing:0.2px;`;
}

function createSettingsModal() {
  const overlay = document.createElement("div");
  overlay.id = "cc-settings-overlay";
  overlay.style.cssText = `
    display:none; position:fixed; inset:0; z-index:11000;
    background:rgba(0,0,0,0.75); backdrop-filter:blur(6px);
    align-items:center; justify-content:center;
  `;

  const box = document.createElement("div");
  box.style.cssText = `
    background:#1e1e2e; border:1px solid #313244; border-radius:16px;
    width:560px; max-width:96vw; max-height:82vh;
    display:flex; flex-direction:column; overflow:hidden;
    box-shadow:0 24px 72px rgba(0,0,0,0.65);
    font-family:system-ui,-apple-system,sans-serif; color:#cdd6f4;
  `;

  box.innerHTML = `
    <div style="padding:15px 20px; background:#25253a; display:flex;
                align-items:center; justify-content:space-between; flex-shrink:0;
                border-radius:16px 16px 0 0;">
      <span style="font-weight:800; font-size:14px; color:#cba6f7; letter-spacing:0.3px;">
        ⚙ Settings
      </span>
      <button id="cc-stg-close"
              style="background:none;border:none;color:#585b70;cursor:pointer;
                     font-size:22px;line-height:1;padding:0 2px;transition:color 0.1s;">×</button>
    </div>

    <!-- Tab bar -->
    <div style="display:flex; gap:0; padding:0 20px; flex-shrink:0;
                border-bottom:1px solid #313244; background:#1e1e2e;">
      <button class="cc-stab" data-tab="providers"
              style="padding:10px 16px; border:none; background:transparent;
                     cursor:pointer; font-size:12px; font-weight:600;
                     border-bottom:2px solid transparent; transition:all 0.15s;">
        🔑 Providers
      </button>
      <button class="cc-stab" data-tab="connection"
              style="padding:10px 16px; border:none; background:transparent;
                     cursor:pointer; font-size:12px; font-weight:600;
                     border-bottom:2px solid transparent; transition:all 0.15s;">
        🔌 Connection
      </button>
      <button class="cc-stab" data-tab="defaults"
              style="padding:10px 16px; border:none; background:transparent;
                     cursor:pointer; font-size:12px; font-weight:600;
                     border-bottom:2px solid transparent; transition:all 0.15s;">
        ⚡ Defaults
      </button>
      <button class="cc-stab" data-tab="appearance"
              style="padding:10px 16px; border:none; background:transparent;
                     cursor:pointer; font-size:12px; font-weight:600;
                     border-bottom:2px solid transparent; transition:all 0.15s;">
        🎨 Appearance
      </button>
    </div>

    <!-- Content -->
    <div id="cc-stg-content"
         style="flex:1; overflow-y:auto; padding:20px; scrollbar-width:thin;
                scrollbar-color:#45475a transparent;"></div>

    <!-- Footer -->
    <div style="padding:10px 20px; border-top:1px solid #313244; flex-shrink:0;
                display:flex; align-items:center; justify-content:space-between;
                background:#1e1e2e; border-radius:0 0 16px 16px;">
      <span style="font-size:11px; color:#45475a;">Changes save automatically</span>
      <button id="cc-stg-done"
              style="padding:7px 20px; border:none; border-radius:8px;
                     background:#cba6f7; color:#1e1e2e; cursor:pointer;
                     font-size:12px; font-weight:700;">Done</button>
    </div>
  `;

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  // ── Tab switching ────────────────────────────────────────────────────────────
  let _activeSettingsTab = "";

  function _activateTab(tab) {
    _activeSettingsTab = tab;
    box.querySelectorAll(".cc-stab").forEach(b => {
      const on = b.dataset.tab === tab;
      b.style.color         = on ? "#cba6f7" : "#585b70";
      b.style.borderBottom  = on ? "2px solid #cba6f7" : "2px solid transparent";
      b.style.background    = "transparent";
    });
    const content = box.querySelector("#cc-stg-content");
    if (tab === "providers")   _renderProvidersTab(content);
    else if (tab === "connection") _renderConnectionTab(content);
    else if (tab === "appearance") _renderAppearanceTab(content);
    else                           _renderDefaultsTab(content);
  }

  box.querySelectorAll(".cc-stab").forEach(b => b.addEventListener("click", () => _activateTab(b.dataset.tab)));
  box.querySelector("#cc-stg-close").addEventListener("click", () => { overlay.style.display = "none"; });
  box.querySelector("#cc-stg-done").addEventListener("click", () => { overlay.style.display = "none"; });
  overlay.addEventListener("click", e => { if (e.target === overlay) overlay.style.display = "none"; });

  // ── Providers tab ────────────────────────────────────────────────────────────
  function _renderProvidersTab(container) {
    container.innerHTML = Object.entries(PROVIDERS).map(([key, prov]) => {
      const apiKey  = escAttr(_getPS(key, "apiKey"));
      const baseUrl = escAttr(_getPS(key, "baseUrl"));
      const hasKey  = !!_getPS(key, "apiKey");
      return `
        <div style="background:#313244; border-radius:12px; padding:14px 16px; margin-bottom:12px;">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
            <span style="font-size:20px; opacity:0.9;">${prov.emoji}</span>
            <span style="font-weight:700; font-size:13px;">${prov.label}</span>
            <span class="cc-key-dot" data-pkey="${key}"
                  style="width:7px;height:7px;border-radius:50%;flex-shrink:0;
                         background:${hasKey ? "#a6e3a1" : "#45475a"};"></span>
            <span style="font-size:11px; color:${hasKey ? "#a6e3a1" : "#45475a"};">
              ${hasKey ? "Key configured" : "No key — uses env var"}
            </span>
          </div>
          <div style="margin-bottom:10px;">
            <label style="${_settingsLabelStyle()}">API Key</label>
            <div style="display:flex;gap:6px;">
              <input class="cc-ps-inp" data-pkey="${key}" data-field="apiKey" type="password"
                     value="${apiKey}" placeholder="Blank = use server env var (recommended)"
                     style="${_settingsInputStyle("flex:1;font-family:monospace;")}">
              <button class="cc-eye" data-pkey="${key}"
                      style="padding:7px 11px;background:#45475a;border:none;
                             border-radius:8px;color:#cdd6f4;cursor:pointer;font-size:14px;
                             flex-shrink:0;" title="Show / hide">👁</button>
            </div>
          </div>
          <div>
            <label style="${_settingsLabelStyle()}">
              Base URL
              <span style="font-weight:400;color:#585b70;font-size:10px;">
                — custom endpoint, Azure, proxy, or self-hosted
              </span>
            </label>
            <input class="cc-ps-inp" data-pkey="${key}" data-field="baseUrl" type="url"
                   value="${baseUrl}"
                   placeholder="Leave blank for the provider's default"
                   style="${_settingsInputStyle()}">
          </div>
        </div>
      `;
    }).join("");

    // Wire input events
    container.querySelectorAll(".cc-ps-inp").forEach(inp => {
      inp.addEventListener("input", () => {
        _setPS(inp.dataset.pkey, inp.dataset.field, inp.value);
        // Update dot in this form
        const hasKey = !!_getPS(inp.dataset.pkey, "apiKey");
        const dot = container.querySelector(`.cc-key-dot[data-pkey="${inp.dataset.pkey}"]`);
        const lbl = dot?.nextElementSibling;
        if (dot) { dot.style.background = hasKey ? "#a6e3a1" : "#45475a"; }
        if (lbl) { lbl.style.color = hasKey ? "#a6e3a1" : "#45475a"; lbl.textContent = hasKey ? "Key configured" : "No key — uses env var"; }
      });
      inp.addEventListener("focus",  () => { inp.style.borderColor = "#cba6f7"; });
      inp.addEventListener("blur",   () => { inp.style.borderColor = "#45475a"; });
    });
    // Eye toggle
    container.querySelectorAll(".cc-eye").forEach(btn => {
      btn.addEventListener("click", () => {
        const inp = container.querySelector(`.cc-ps-inp[data-pkey="${btn.dataset.pkey}"][data-field="apiKey"]`);
        if (inp) inp.type = inp.type === "password" ? "text" : "password";
      });
    });
  }

  // ── Connection tab ───────────────────────────────────────────────────────────
  function _renderConnectionTab(container) {
    const wsUrl    = escAttr(localStorage.getItem("comfyclaw_ws_url") || DEFAULT_WS_URL);
    const comfyUrl = escAttr(localStorage.getItem("comfyclaw_comfyui_addr") || "127.0.0.1:8000");
    container.innerHTML = `
      <div style="margin-bottom:16px;">
        <label style="${_settingsLabelStyle()}">ComfyClaw WebSocket URL</label>
        <input id="cc-conn-ws" type="text" value="${wsUrl}" placeholder="${DEFAULT_WS_URL}"
               style="${_settingsInputStyle("font-family:monospace;")}">
        <p style="margin:5px 0 0; font-size:11px; color:#585b70;">
          The WebSocket address of the running <code>comfyclaw serve</code> process.
          Changing this reloads the page.
        </p>
      </div>
      <div>
        <label style="${_settingsLabelStyle()}">ComfyUI Server Address</label>
        <input id="cc-conn-comfy" type="text" value="${comfyUrl}" placeholder="127.0.0.1:8000"
               style="${_settingsInputStyle("font-family:monospace;")}">
        <p style="margin:5px 0 0; font-size:11px; color:#585b70;">
          Used by the Debug Agent to query node schemas. Normally the same host ComfyUI is running on.
        </p>
      </div>
    `;
    container.querySelectorAll("input").forEach(inp => {
      inp.addEventListener("focus",  () => inp.style.borderColor = "#cba6f7");
      inp.addEventListener("blur",   () => inp.style.borderColor = "#45475a");
    });
    container.querySelector("#cc-conn-ws")?.addEventListener("change", e => {
      const val = e.target.value.trim();
      if (val) { localStorage.setItem("comfyclaw_ws_url", val); window.location.reload(); }
    });
    container.querySelector("#cc-conn-comfy")?.addEventListener("change", e => {
      localStorage.setItem("comfyclaw_comfyui_addr", e.target.value.trim());
    });
  }

  // ── Defaults tab ─────────────────────────────────────────────────────────────
  function _renderAppearanceTab(container) {
    const theme = localStorage.getItem("comfyclaw_theme") || "dark";
    const dock  = localStorage.getItem("comfyclaw_dock_mode") || "comfy-sidebar";
    const selectStyle = _settingsInputStyle("cursor:pointer;");
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;">
        <div>
          <label style="${_settingsLabelStyle()}">Theme</label>
          <select id="cc-stg-theme" style="${selectStyle}">
            <option value="dark"  ${theme==="dark"  ? "selected":""}>🌙 Dark</option>
            <option value="light" ${theme==="light" ? "selected":""}>☀ Light</option>
          </select>
        </div>
        <div>
          <label style="${_settingsLabelStyle()}">Panel position</label>
          <select id="cc-stg-dock" style="${selectStyle}">
            <option value="comfy-sidebar" ${dock==="comfy-sidebar" ? "selected":""}>Inside ComfyUI sidebar (recommended)</option>
            <option value="sidebar"       ${dock==="sidebar"       ? "selected":""}>Right-rail dock</option>
            <option value="float"         ${dock==="float"         ? "selected":""}>Floating widget</option>
          </select>
          <p style="margin:4px 0 0; font-size:11px; color:#585b70;">
            Inside ComfyUI sidebar: mounts as a native tab. Right-rail: pinned
            to the right edge. Floating: draggable widget.
          </p>
        </div>
      </div>
    `;
    container.querySelector("#cc-stg-theme")?.addEventListener("change", (e) => {
      const v = e.target.value;
      localStorage.setItem("comfyclaw_theme", v);
      document.documentElement.setAttribute("data-cc-theme", v);
      // Keep the hidden header button in sync too in case future code reads it.
      const themeBtn = document.getElementById("comfyclaw-theme-btn");
      if (themeBtn) themeBtn.textContent = v === "light" ? "☀" : "🌙";
    });
    container.querySelector("#cc-stg-dock")?.addEventListener("change", (e) => {
      const v = e.target.value;
      localStorage.setItem("comfyclaw_dock_mode", v);
      if (_clawPanel?._reapplyDock) _clawPanel._reapplyDock();
    });
  }

  function _renderDefaultsTab(container) {
    const iters    = localStorage.getItem("comfyclaw-gen-iters")    || "3";
    const opDelay  = localStorage.getItem("comfyclaw-gen-opdelay")  || "400";
    const verifier = localStorage.getItem("comfyclaw-gen-verifier") || "vlm";
    const vmodel   = localStorage.getItem("comfyclaw-gen-vmodel")   || "";

    const selectStyle = _settingsInputStyle("cursor:pointer;");
    container.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;">
        <div>
          <label style="${_settingsLabelStyle()}">Default Iterations</label>
          <input id="cc-def-iters" type="number" min="1" max="20" value="${iters}"
                 style="${_settingsInputStyle("width:80px;")}">
          <p style="margin:4px 0 0; font-size:11px; color:#585b70;">
            How many agent–generate–verify cycles to run per request.
          </p>
        </div>
        <div>
          <label style="${_settingsLabelStyle()}">Default Verifier Mode</label>
          <select id="cc-def-verifier" style="${selectStyle}">
            <option value="vlm"    ${verifier==="vlm"    ? "selected":""}>VLM (automatic)</option>
            <option value="human"  ${verifier==="human"  ? "selected":""}>Human-in-the-loop</option>
            <option value="hybrid" ${verifier==="hybrid" ? "selected":""}>Hybrid</option>
          </select>
        </div>
        <div>
          <label style="${_settingsLabelStyle()}">Default Verifier Model</label>
          <select id="cc-def-vmodel" style="${selectStyle}">
            <option value=""                               ${!vmodel ? "selected":""}>Same as Agent model</option>
            <option value="anthropic/claude-sonnet-4-5"   ${vmodel==="anthropic/claude-sonnet-4-5"   ? "selected":""}>Claude Sonnet 4.5</option>
            <option value="openai/gpt-4o"                 ${vmodel==="openai/gpt-4o"                 ? "selected":""}>GPT-4o</option>
            <option value="gemini/gemini-2.5-flash"       ${vmodel==="gemini/gemini-2.5-flash"       ? "selected":""}>Gemini 2.5 Flash</option>
          </select>
        </div>
        <div>
          <label style="${_settingsLabelStyle()}">Op Delay (ms)</label>
          <input id="cc-def-opdelay" type="number" min="0" max="2000" value="${opDelay}"
                 style="${_settingsInputStyle("width:90px;")}">
          <p style="margin:4px 0 0; font-size:11px; color:#585b70;">
            Milliseconds between node add/update operations on the canvas.
            Set to 0 for instant.
          </p>
        </div>
      </div>
    `;

    const sync = (id, lsKey, panelId) => {
      const el = container.querySelector(id);
      if (!el) return;
      el.addEventListener("focus", () => el.style.borderColor = "#cba6f7");
      el.addEventListener("blur",  () => el.style.borderColor = "#45475a");
      el.addEventListener("change", () => {
        const v = el.value;
        localStorage.setItem(lsKey, v);
        const panelEl = document.getElementById(panelId);
        if (panelEl) panelEl.value = v;
        if (lsKey === "comfyclaw-gen-opdelay")
          localStorage.setItem("comfyclaw_op_delay", v);
      });
    };
    sync("#cc-def-iters",    "comfyclaw-gen-iters",    "comfyclaw-gen-iters");
    sync("#cc-def-verifier", "comfyclaw-gen-verifier",  "comfyclaw-gen-verifier");
    sync("#cc-def-vmodel",   "comfyclaw-gen-vmodel",    "comfyclaw-gen-vmodel");
    sync("#cc-def-opdelay",  "comfyclaw-gen-opdelay",   "comfyclaw-gen-opdelay");
  }

  // Public
  overlay.openTo = (tab = "providers") => {
    overlay.style.display = "flex";
    _activateTab(tab);
  };

  _activateTab("providers");
  return overlay;
}

let _settingsModal = null;

// ─────────────────────────────────────────────────────────────────────────────
// Connection dot helpers
// ─────────────────────────────────────────────────────────────────────────────

function _updateConnDot(state, countdown = 0) {
  const dot = document.getElementById("cc-conn-dot");
  if (!dot) return;
  const cfg = {
    connected:    { color:"var(--cc-accent-green)",  halo:"rgba(166,227,161,0.22)",
                    title:"Connected",   anim:"" },
    connecting:   { color:"var(--cc-accent-yellow)", halo:"rgba(249,226,175,0.22)",
                    title:"Connecting…", anim:"cc-pulse" },
    reconnecting: { color:"var(--cc-accent-orange)", halo:"rgba(250,179,135,0.22)",
                    title:`Reconnecting in ${countdown}s…`, anim:"cc-pulse" },
    disconnected: { color:"var(--cc-accent-red)",    halo:"rgba(243,139,168,0.22)",
                    title:"Disconnected — click to retry", anim:"" },
  };
  const c = cfg[state] || cfg.disconnected;
  dot.style.background = c.color;
  dot.style.boxShadow  = `0 0 0 2px ${c.halo}`;
  const baseTitle = countdown > 0 ? `Reconnecting in ${countdown}s…` : c.title;
  dot.dataset.baseTitle = baseTitle;
  dot.title = _nodeCount > 0 ? `${baseTitle} · ${_nodeCount} nodes` : baseTitle;
  dot.className = c.anim;
  dot.dataset.state = state;
}

function _updateNodeCount(n) {
  _nodeCount = n;
  // Surface the value through the connection-dot tooltip instead of a pill
  // in the header — keeps the bar compact.
  const dot = document.getElementById("cc-conn-dot");
  if (dot) {
    const base = (dot.dataset.baseTitle || dot.title || "ComfyClaw").split(" · ")[0];
    dot.dataset.baseTitle = base;
    dot.title = n > 0 ? `${base} · ${n} nodes` : base;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Provider picker
// ─────────────────────────────────────────────────────────────────────────────

function _setActiveProvider(key, updateSession = true) {
  const stateEl = document.getElementById("comfyclaw-provider-state");
  if (stateEl) stateEl.dataset.provider = key;
  // Highlight buttons
  document.querySelectorAll(".cc-provider-btn").forEach(btn => {
    const isActive = btn.dataset.key === key;
    const c = PROVIDERS[btn.dataset.key]?.color || "#45475a";
    btn.style.borderColor  = isActive ? c : "#45475a";
    btn.style.background   = isActive ? c + "22" : "transparent";
    btn.style.color        = isActive ? c : "#585b70";
    btn.style.fontWeight   = isActive ? "700" : "500";
  });
  // Populate model dropdown
  const modelEl = document.getElementById("comfyclaw-gen-model");
  if (!modelEl) return;
  const prov = PROVIDERS[key];
  if (!prov) return;
  const prev = modelEl.value;
  modelEl.innerHTML = `<option value="">Server default</option>`
    + prov.models.map(m => `<option value="${m.value}">${m.label}</option>`).join("");
  // Restore previous selection if still valid
  if (prev && modelEl.querySelector(`[value="${prev}"]`)) modelEl.value = prev;
  if (updateSession) _captureCurrentSession();
  _refreshProvDots();
}

// ─────────────────────────────────────────────────────────────────────────────
// Markdown renderer (lightweight, no external deps)
// ─────────────────────────────────────────────────────────────────────────────

function renderMarkdown(raw) {
  if (!raw) return "";

  // Extract fenced code blocks first so their content isn't processed
  const blocks = [];
  let text = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = blocks.length;
    const escaped = code.trim()
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const langLabel = lang ? `<span style="color:#585b70;font-size:10px;font-family:monospace;">${lang}</span>` : "";
    // Copy button uses data-code attr (base64 encoded raw code)
    const raw64 = btoa(unescape(encodeURIComponent(code.trim())));
    blocks.push(
      `<div class="cc-code-block" style="position:relative;margin:6px 0;">`
      + (langLabel ? `<div style="padding:4px 10px 2px;background:var(--cc-surface);border-radius:6px 6px 0 0;">${langLabel}</div>` : "")
      + `<pre style="background:var(--cc-surface-2);border-radius:${lang?"0 0 6px 6px":"6px"};padding:8px 10px;color:var(--cc-fg);`
      + `overflow-x:auto;margin:0;font-size:11px;line-height:1.5;">`
      + `<code style="font-family:monospace;">${escaped}</code></pre>`
      + `<button class="cc-copy-btn" data-b64="${raw64}">Copy</button>`
      + `</div>`
    );
    return `\x00BLK${idx}\x00`;
  });

  // Escape remaining HTML
  text = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Inline code
  text = text.replace(/`([^`\n]+)`/g,
    `<code style="background:var(--cc-surface-2);border-radius:3px;padding:1px 5px;color:var(--cc-fg);`
    + `font-size:11px;font-family:monospace;">$1</code>`);

  // Bold + italic
  text = text.replace(/\*\*\*(.*?)\*\*\*/g, "<strong><em>$1</em></strong>");
  text = text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__(.*?)__/g, "<strong>$1</strong>");
  text = text.replace(/\*(.*?)\*/g, "<em>$1</em>");

  // Headings (line-start only)
  text = text.replace(/^### (.+)/gm,
    `<div style="font-weight:700;color:#cba6f7;margin:6px 0 2px;font-size:12px;">$1</div>`);
  text = text.replace(/^## (.+)/gm,
    `<div style="font-weight:700;color:#cba6f7;margin:8px 0 2px;font-size:13px;">$1</div>`);
  text = text.replace(/^# (.+)/gm,
    `<div style="font-weight:700;color:#cba6f7;margin:10px 0 4px;font-size:14px;">$1</div>`);

  // Bullet lists
  text = text.replace(/^[ \t]*[*\-] (.+)/gm,
    `<div style="padding-left:14px;margin:1px 0;">• $1</div>`);
  text = text.replace(/^[ \t]*\d+\. (.+)/gm,
    `<div style="padding-left:14px;margin:1px 0;">$&</div>`);

  // Horizontal rule
  text = text.replace(/^---+$/gm,
    `<hr style="border:none;border-top:1px solid #45475a;margin:6px 0;">`);

  // Newlines → <br>
  text = text.replace(/\n/g, "<br>");

  // Restore code blocks
  text = text.replace(/\x00BLK(\d+)\x00/g, (_, i) => blocks[parseInt(i)]);

  return text;
}

// ─────────────────────────────────────────────────────────────────────────────
// Unified ComfyClaw panel  (controls + chat/log in one draggable widget)
// ─────────────────────────────────────────────────────────────────────────────

let _clawPanel        = null;
let _thinkingPanel    = null;   // alias — always same as _clawPanel
let _clawPanelRunning = false;
let _thinkingEntries  = [];
const MAX_LOG_ENTRIES = 200;

// Per-event coloring for the agent log.  Colors flow through CSS variables
// so the design tokens in styles.js stay the single source of truth.
const EVENT_STYLES = {
  strategy:         { icon: "🧠", color: "var(--cc-accent)",        label: "Strategy"   },
  tool_call:        { icon: "🔧", color: "var(--cc-accent-blue)",   label: "Tool Call"  },
  tool_result:      { icon: "📋", color: "var(--cc-fg-muted)",      label: "Result"     },
  thinking:         { icon: "💭", color: "var(--cc-accent-yellow)", label: "Thinking"   },
  validation:       { icon: "✓",  color: "var(--cc-accent-green)",  label: "Validation" },
  error:            { icon: "❌", color: "var(--cc-accent-red)",    label: "Error"      },
  info:             { icon: "ℹ",  color: "var(--cc-accent-blue)",   label: "Info"       },
  user:             { icon: "👤", color: "var(--cc-accent-orange)", label: "You"        },
  assistant_stream: { icon: "🤖", color: "var(--cc-accent-green)",  label: "ComfyClaw"  },
  assistant_done:   { icon: "🤖", color: "var(--cc-accent-green)",  label: "ComfyClaw"  },
};

function createComfyClawPanel() {
  const panel = document.createElement("div");
  panel.id = "comfyclaw-panel";
  Object.assign(panel.style, {
    position:      "fixed",
    top:           "60px",
    right:         "12px",
    width:         "400px",
    maxHeight:     "88vh",
    zIndex:        "9998",
    background:    "var(--cc-bg)",
    color:         "var(--cc-fg)",
    borderRadius:  "var(--cc-radius)",
    boxShadow:     "var(--cc-shadow)",
    border:        "1px solid var(--cc-border)",
    fontFamily:    "system-ui, -apple-system, sans-serif",
    fontSize:      "13px",
    lineHeight:    "1.5",
    display:       "flex",
    flexDirection: "column",
    overflow:      "hidden",
  });

  panel.innerHTML = `
    <!-- ── Header ─────────────────────────────────────────────────── -->
    <div id="comfyclaw-gen-header"
         style="padding:9px 12px; background:var(--cc-surface); cursor:grab; flex-shrink:0;
                display:flex; justify-content:space-between; align-items:center;
                user-select:none; border-radius:var(--cc-radius) var(--cc-radius) 0 0; gap:8px;
                border-bottom:1px solid var(--cc-border);">
      <!-- Compact left cluster: connection dot only. Logo + node count
           moved out — the ComfyUI sidebar tab title already says "ComfyClaw",
           and the dot's tooltip now carries the node count. -->
      <span id="cc-conn-dot"
            title="Connecting… (tab: ${_CONNECTION_ID})"
            style="width:8px;height:8px;border-radius:50%;background:var(--cc-accent-yellow);
                   flex-shrink:0;cursor:pointer;
                   box-shadow:0 0 0 2px rgba(249,226,175,0.18);"></span>
      <span id="cc-node-count" style="display:none;"></span>
      <div style="display:flex; gap:2px; align-items:center; flex:1; min-width:0;">
        <div id="comfyclaw-sessions-tabs"
             style="display:flex; gap:2px; flex:1; overflow-x:auto; min-width:0;
                    scrollbar-width:none;"></div>
        <button id="comfyclaw-new-session-btn" class="cc-icon-btn cc-icon-btn-sm"
                title="New session"
                style="font-size:14px;font-weight:600;">+</button>
      </div>
      <!-- Right cluster: theme + dock moved into Settings modal (Appearance
           tab). Only the always-relevant actions remain here. -->
      <div style="display:flex; align-items:center; gap:1px; flex-shrink:0;">
        <button id="comfyclaw-theme-btn" class="cc-icon-btn cc-icon-btn-sm cc-header-toggle"
                title="Toggle theme" style="display:none;">🌙</button>
        <button id="comfyclaw-dock-btn" class="cc-icon-btn cc-icon-btn-sm cc-header-toggle"
                title="Cycle dock mode" style="display:none;">⌷</button>
        <button id="comfyclaw-settings-btn" class="cc-icon-btn cc-icon-btn-sm"
                title="Open settings">⚙</button>
        <button id="comfyclaw-ctrl-toggle" class="cc-icon-btn cc-icon-btn-sm"
                title="Collapse / expand controls"
                style="transition:transform 0.18s var(--cc-ease);">▾</button>
        <button id="comfyclaw-close-btn" class="cc-icon-btn cc-icon-btn-sm"
                title="Hide panel">×</button>
      </div>
    </div>

    <!-- ── Controls (collapsible) ──────────────────────────────────── -->
    <div id="comfyclaw-gen-body"
         style="padding:12px 14px 10px; flex-shrink:0;
                max-height:380px; overflow-y:auto;">
      <!-- Prompt textarea + quick prompts are hidden: the composer below is
           the single prompt input. Kept in DOM so legacy reads still work. -->
      <textarea id="comfyclaw-gen-prompt" class="cc-textarea" rows="3"
        placeholder="Describe what you want to generate…"
        style="display:none;"></textarea>
      <div id="cc-quick-prompts" style="display:none;"></div>

      <!-- Hidden strategy buttons — the composer's strategy chip drives selection. -->
      <div id="comfyclaw-gen-mode" style="display:none;">
        <button data-mode="scratch" class="comfyclaw-mode-btn"></button>
        <button data-mode="improve" class="comfyclaw-mode-btn"></button>
      </div>

      <!-- Hidden model picker — the composer's model chip is the single
           selector. Kept in DOM because many code paths read .value here. -->
      <div style="display:none;">
        <div id="comfyclaw-provider-bar"></div>
        <select id="comfyclaw-gen-model" class="cc-select"></select>
        <div id="comfyclaw-provider-state" data-provider="anthropic"></div>
      </div>

      <details id="comfyclaw-adv-details" style="margin-bottom:10px;">
        <summary style="cursor:pointer; user-select:none; list-style:none;
                        display:flex; align-items:center; gap:5px;
                        color:var(--cc-fg-dim); font-size:10px; font-weight:700;
                        letter-spacing:0.5px; text-transform:uppercase;">
          <span id="comfyclaw-adv-arrow"
                style="display:inline-block;transition:transform 0.18s var(--cc-ease);">▸</span>
          Advanced
          <span id="comfyclaw-adv-mode-pill" class="cc-pill"
                style="margin-left:6px; padding:1px 6px; font-size:9px;
                       letter-spacing:0.3px; text-transform:none;">auto</span>
        </summary>
        <div style="padding-top:8px; display:flex; flex-direction:column; gap:6px;">
          <!-- Iterations / Verifier / Verifier Model are only meaningful when
               the agent is allowed to loop — Manual is a single shot. -->
          <div class="cc-row cc-adv-row" data-modes="auto,copilot" style="gap:8px;">
            <label style="color:var(--cc-fg-muted); font-size:11px; min-width:80px;">Iterations</label>
            <input id="comfyclaw-gen-iters" class="cc-input" type="number" min="1" max="20" value="3"
              style="width:62px;flex:none;padding:4px 7px;font-size:12px;">
          </div>
          <div class="cc-row cc-adv-row" data-modes="auto,copilot" style="gap:8px;">
            <label style="color:var(--cc-fg-muted); font-size:11px; min-width:80px;">Verifier</label>
            <select id="comfyclaw-gen-verifier" class="cc-select"
                    style="flex:1;padding:4px 7px;font-size:11px;">
              <option value="vlm">VLM (auto)</option>
              <option value="human">Human</option>
              <option value="hybrid">Hybrid</option>
            </select>
          </div>
          <div class="cc-row cc-adv-row" data-modes="auto,copilot" style="gap:8px;">
            <label style="color:var(--cc-fg-muted); font-size:11px; min-width:80px;">Verifier Model</label>
            <select id="comfyclaw-gen-vmodel" class="cc-select"
                    style="flex:1;padding:4px 7px;font-size:11px;">
              <option value="">Same as Agent</option>
              <option value="anthropic/claude-sonnet-4-5">Claude Sonnet 4.5</option>
              <option value="openai/gpt-4o">GPT-4o</option>
              <option value="gemini/gemini-2.5-flash">Gemini 2.5 Flash</option>
            </select>
          </div>
          <!-- Always-relevant rows live below the mode-gated ones. -->
          <div class="cc-row cc-adv-row" data-modes="manual,auto,copilot" style="gap:8px;">
            <label style="color:var(--cc-fg-muted); font-size:11px; min-width:80px;">API Key</label>
            <input id="comfyclaw-gen-apikey" class="cc-input" type="password" placeholder="(server default)"
              style="flex:1;padding:4px 7px;font-size:11px;font-family:monospace;">
          </div>
          <div class="cc-row cc-adv-row" data-modes="manual,auto,copilot" style="gap:8px;">
            <label style="color:var(--cc-fg-muted); font-size:11px; min-width:80px;">Op Delay (ms)</label>
            <input id="comfyclaw-gen-opdelay" class="cc-input" type="number" min="0" max="2000" value="400"
              style="width:72px;flex:none;padding:4px 7px;font-size:12px;">
          </div>
          <div class="cc-row cc-adv-row" data-modes="manual,auto,copilot" style="gap:8px;"
               title="Build workflow only — skip ComfyUI image generation. Great for fast iteration on the agent loop without burning GPU.">
            <label style="color:var(--cc-fg-muted); font-size:11px; min-width:80px;">Debug Mode</label>
            <label style="display:flex; align-items:center; gap:6px;
                          color:var(--cc-fg); font-size:11px; cursor:pointer;">
              <input id="comfyclaw-gen-dryrun" type="checkbox"
                style="margin:0; accent-color:var(--cc-accent-yellow); cursor:pointer;">
              <span>Build workflow only (no image)</span>
            </label>
          </div>
          <!-- Empty-state hint shown in Manual mode after we hide the loop knobs. -->
          <div id="comfyclaw-adv-manual-hint"
               style="display:none; color:var(--cc-fg-dim); font-size:11px;
                      padding:4px 0 2px; line-height:1.45;">
            ✋ Manual mode runs one round with no verifier. Iterations and
            verifier settings only apply to Auto / Co-pilot.
          </div>
        </div>
      </details>

      <!-- Checkpoint strip -->
      <div id="comfyclaw-cp-section" style="margin-top:8px; display:none;">
        <div style="display:flex; justify-content:space-between; align-items:center;
                    margin-bottom:4px;">
          <span class="cc-label" style="margin:0;">📸 Checkpoints</span>
          <button id="comfyclaw-cp-save-btn" class="cc-btn cc-btn-secondary"
                  style="padding:3px 8px;font-size:10px;">
            + Save
          </button>
        </div>
        <div id="comfyclaw-cp-list"
             style="max-height:110px; overflow-y:auto; display:flex;
                    flex-direction:column; gap:3px;"></div>
      </div>
    </div>

    <!-- ── Sticky action bar (always visible) ───────────────────────── -->
    <div id="comfyclaw-action-bar">
      <div class="cc-action-row">
        <div class="cc-action-primary">
          <button id="comfyclaw-gen-btn" class="cc-btn cc-btn-primary"
                  title="Send the prompt to the agent and trigger the workflow">
            <span style="font-size:13px;">▶</span><span>Generate</span>
          </button>
          <button id="comfyclaw-gen-stop" class="cc-btn cc-btn-danger"
                  style="display:none;">
            <span style="font-size:11px;">■</span><span>Stop</span>
          </button>
        </div>
        <button id="comfyclaw-debug-btn" class="cc-btn cc-btn-warn"
                title="Audit the current workflow with the agent">
          🔍 Audit
        </button>
      </div>
      <div id="cc-gen-progress" class="cc-progress" style="display:none;">
        <div class="cc-progress-bar"></div>
      </div>
      <div id="comfyclaw-gen-status" class="cc-status-pill" data-state="idle"
           style="display:none; margin-top:0;">
        <span id="comfyclaw-gen-status-text" style="overflow:hidden;
              text-overflow:ellipsis;white-space:nowrap;flex:1;"></span>
        <span id="cc-gen-timer" class="cc-pill cc-pill-mono"
              style="display:none;">0:00</span>
      </div>
    </div>

    <!-- ── Chat / Agent Log ────────────────────────────────────────── -->
    <div id="comfyclaw-think-body"
         style="display:flex; flex-direction:column; flex:1; min-height:180px;
                overflow:hidden; border-top:1px solid var(--cc-border);">
      <!-- Log section header -->
      <div style="display:flex; align-items:center; justify-content:space-between;
                  padding:6px 12px 4px; flex-shrink:0;">
        <span class="cc-label" style="margin:0;">Chat &amp; Agent Log</span>
        <div style="display:flex; gap:4px; align-items:center;">
          <span id="comfyclaw-think-count" class="cc-pill cc-pill-mono"
                style="display:none;"></span>
          <button id="comfyclaw-clear-log" class="cc-icon-btn cc-icon-btn-sm"
                  title="Clear log">🗑</button>
        </div>
      </div>
      <div style="position:relative; flex:1; min-height:0; display:flex; flex-direction:column;">
        <div id="comfyclaw-think-log"
             style="flex:1; overflow-y:auto; padding:6px 10px 8px;
                    scroll-behavior:smooth; min-height:0;
                    scrollbar-width:thin; scrollbar-color:var(--cc-border) transparent;"></div>
        <!-- Scroll-to-bottom button -->
        <button id="cc-scroll-bottom" title="Scroll to bottom"
                style="position:absolute; bottom:10px; right:12px;
                       width:28px; height:28px; border-radius:50%;
                       background:var(--cc-surface-2); border:1px solid var(--cc-border);
                       color:var(--cc-fg); cursor:pointer; font-size:13px;
                       display:none; align-items:center; justify-content:center;
                       box-shadow:var(--cc-shadow-sm); z-index:2;
                       transition:transform 0.15s, background 0.15s;">↓</button>
      </div>
      <div id="comfyclaw-think-input-area"
           style="padding:8px 10px 10px; border-top:1px solid var(--cc-border); flex-shrink:0;
                  background:var(--cc-surface-tint);">
        <div class="cc-composer-card">
          <div id="cc-composer-progress" class="cc-progress cc-composer-progress"
               style="display:none;"><div class="cc-progress-bar"></div></div>
          <textarea id="comfyclaw-think-input" rows="1"
            placeholder="Ask ComfyClaw, or describe what to generate…"></textarea>
          <div id="cc-composer-status" class="cc-composer-status" style="display:none;">
            <span id="cc-composer-status-text"></span>
            <span id="cc-composer-timer" class="cc-pill cc-pill-mono" style="display:none;">0:00</span>
          </div>
          <div class="cc-composer-toolbar">
            <button id="cc-composer-backend-chip" class="cc-composer-chip"
                    title="Click to change agent backend">
              <span class="cc-chip-icon">⚙</span>
              <span class="cc-chip-label">LiteLLM</span>
              <span class="cc-chip-chev">▾</span>
            </button>
            <button id="cc-composer-model-chip" class="cc-composer-chip"
                    title="Click to change model">
              <span class="cc-chip-dot"></span>
              <span class="cc-chip-label">Server default</span>
              <span class="cc-chip-chev">▾</span>
            </button>
            <button id="cc-composer-strategy-chip" class="cc-composer-chip"
                    title="Click to toggle build strategy">
              <span class="cc-chip-icon">✨</span>
              <span class="cc-chip-label">Scratch</span>
            </button>
            <button id="cc-composer-audit" class="cc-composer-btn"
                    title="Audit current workflow"
                    style="width:auto;padding:0 8px;font-size:11px;">🔍</button>
            <div style="flex:1;"></div>
            <button id="cc-composer-run" class="cc-composer-btn cc-composer-btn-run"
                    title="Run generation with this prompt">▶</button>
            <button id="cc-composer-stop" class="cc-composer-btn cc-composer-btn-stop"
                    title="Stop generation"
                    style="display:none;">■</button>
            <button id="comfyclaw-think-send" class="cc-composer-btn cc-composer-btn-primary"
                    title="Send to ComfyClaw (Enter)">↑</button>
          </div>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(panel);

  // ── Provider icon bar ────────────────────────────────────────────────────────
  const provBar = panel.querySelector("#comfyclaw-provider-bar");
  Object.entries(PROVIDERS).forEach(([key, prov]) => {
    const btn = document.createElement("button");
    btn.className = "cc-provider-btn";
    btn.dataset.key = key;
    btn.title = prov.label;
    btn.style.cssText = `
      display:flex; align-items:center; gap:4px; padding:4px 9px;
      border-radius:7px; border:1px solid var(--cc-border); background:transparent;
      color:var(--cc-fg-dim); cursor:pointer; font-size:11px; font-weight:500;
      transition: border-color 0.15s, background 0.15s, color 0.15s; flex-shrink:0;
    `;
    btn.innerHTML = `<span style="font-size:12px;">${prov.emoji}</span><span>${prov.label}</span>`;
    btn.addEventListener("click", () => {
      _setActiveProvider(key);
      const sess = _activeSession();
      sess.provider = key; _persistSessions();
    });
    provBar.appendChild(btn);
  });

  // ── Advanced summary toggle arrow ─────────────────────────────────────────────
  // CSS rotates the same glyph instead of swapping characters so the
  // affordance reads as a single chevron flipping.
  panel.querySelector("details")?.addEventListener("toggle", ev => {
    const arrow = panel.querySelector("#comfyclaw-adv-arrow");
    if (arrow) arrow.style.transform = ev.target.open ? "rotate(90deg)" : "rotate(0deg)";
  });

  // ── New session button ────────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-new-session-btn").addEventListener("click", _newSession);

  // ── Clear log button ──────────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-clear-log").addEventListener("click", () => {
    _chatHistory = [];
    const sess = _activeSession();
    sess.chatHistory = []; _persistSessions();
    clearAgentLog();
    showToast("Log cleared", "info", 1500);
  });

  // ── Scroll-to-bottom button ───────────────────────────────────────────────────
  const logEl2    = panel.querySelector("#comfyclaw-think-log");
  const scrollBtn = panel.querySelector("#cc-scroll-bottom");
  if (logEl2 && scrollBtn) {
    logEl2.addEventListener("scroll", () => {
      const atBottom = logEl2.scrollHeight - logEl2.scrollTop - logEl2.clientHeight < 80;
      scrollBtn.style.display = atBottom ? "none" : "flex";
    });
    scrollBtn.addEventListener("click", () => {
      logEl2.scrollTop = logEl2.scrollHeight;
    });
  }

  // ── Quick-prompt chips ────────────────────────────────────────────────────────
  const QUICK_PROMPTS = [
    { label:"📷 Text→Image",  text:"A cinematic photo of a cat wearing sunglasses, golden hour, photorealistic" },
    { label:"🖼 Img→Img",     text:"Improve the composition and lighting of the current workflow" },
    { label:"🎨 Add LoRA",    text:"Add a LoRA loader node and connect it to the model, keep everything else intact" },
    { label:"🔍 Debug",       text:"Find and explain any errors or missing connections in the workflow" },
    { label:"⚡ SDXL",        text:"Build a standard SDXL text-to-image workflow with DPM++ 2M Karras sampler" },
    { label:"🎬 AnimateDiff", text:"Create an AnimateDiff video generation workflow with motion module" },
  ];
  const chipsBar = panel.querySelector("#cc-quick-prompts");
  const promptTA = panel.querySelector("#comfyclaw-gen-prompt");
  if (chipsBar && promptTA) {
    QUICK_PROMPTS.forEach(qp => {
      const chip = document.createElement("button");
      chip.className = "cc-chip";
      chip.textContent = qp.label;
      chip.title = qp.text;
      chip.style.fontSize = "10px";
      chip.style.padding  = "3px 8px";
      chip.addEventListener("click", () => {
        promptTA.value = qp.text;
        promptTA.focus();
        // Save to session
        _activeSession().prompt = qp.text;
        _persistSessions();
        // Trigger persistence + history side-effects of the prompt input.
        promptTA.dispatchEvent(new Event("input", { bubbles: true }));
      });
      chipsBar.appendChild(chip);
    });
  }

  // ── Initialize session tabs + provider for active session ─────────────────────
  _renderSessionTabs();
  const initSess = _activeSession();
  _setActiveProvider(initSess.provider || "anthropic", false);
  if (initSess.model) {
    const mEl = panel.querySelector("#comfyclaw-gen-model");
    if (mEl && mEl.querySelector(`[value="${initSess.model}"]`)) mEl.value = initSess.model;
  }
  if (initSess.prompt) {
    const pEl = panel.querySelector("#comfyclaw-gen-prompt");
    if (pEl) pEl.value = initSess.prompt;
  }
  _chatHistory = [...(initSess.chatHistory || [])];

  // ── Strategy toggle (✨ From Scratch / 🔧 Improve) ───────────────────────────
  let selectedMode = "scratch";
  const modeContainer = panel.querySelector("#comfyclaw-gen-mode");
  function _paintStrategyButtons() {
    modeContainer.querySelectorAll(".comfyclaw-mode-btn").forEach((b) => {
      const active = b.dataset.mode === selectedMode;
      b.style.borderColor = active ? "var(--cc-accent)" : "var(--cc-border)";
      b.style.background  = active ? "rgba(203,166,247,0.13)" : "var(--cc-surface-2)";
      b.style.color       = active ? "var(--cc-accent)" : "var(--cc-fg)";
    });
  }
  modeContainer.querySelectorAll(".comfyclaw-mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedMode = btn.dataset.mode;
      _paintStrategyButtons();
    });
  });
  _paintStrategyButtons();

  // ── Settings button ───────────────────────────────────────────────────────────
  // Hover styling lives in styles.js (.cc-icon-btn:hover) so we only need
  // the click handler here.
  const settingsBtn = panel.querySelector("#comfyclaw-settings-btn");
  settingsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!_settingsModal) _settingsModal = createSettingsModal();
    _settingsModal.openTo("providers");
  });

  // ── Connection dot — click to manually retry ──────────────────────────────
  panel.querySelector("#cc-conn-dot")?.addEventListener("click", () => {
    if (_activeSyncClient?.ws?.readyState === WebSocket.OPEN) return;
    _activeSyncClient?.destroy();
    _activeSyncClient = new SyncClient();
    _activeSyncClient.connect();
    showToast("Reconnecting…", "info", 1500);
  });

  // ── Controls toggle (▾ button in header) ─────────────────────────────────────
  // The button always shows "▾" and we rotate it 180° when collapsed so the
  // affordance reads as a single chevron flipping, not a glyph swap.
  const ctrlSection = panel.querySelector("#comfyclaw-gen-body");
  const ctrlToggle  = panel.querySelector("#comfyclaw-ctrl-toggle");
  let ctrlCollapsed = false;
  function _paintCtrl() {
    ctrlSection.style.display = ctrlCollapsed ? "none" : "";
    ctrlToggle.style.transform = ctrlCollapsed ? "rotate(-90deg)" : "rotate(0deg)";
    ctrlToggle.title = ctrlCollapsed ? "Expand controls" : "Collapse controls";
  }
  ctrlToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    ctrlCollapsed = !ctrlCollapsed;
    _paintCtrl();
    localStorage.setItem("comfyclaw_ctrl_collapsed", ctrlCollapsed ? "1" : "0");
  });
  // New default: collapsed (composer is the primary surface). Existing users
  // who already have a preference saved keep their choice.
  const _savedCtrl = localStorage.getItem("comfyclaw_ctrl_collapsed");
  if (_savedCtrl === "1" || _savedCtrl === null) {
    ctrlCollapsed = true;
  }
  _paintCtrl();

  // ── Theme (light / dark) ────────────────────────────────────────────────────
  const themeBtn = panel.querySelector("#comfyclaw-theme-btn");
  function _applyTheme(t) {
    document.documentElement.setAttribute("data-cc-theme", t);
    if (themeBtn) {
      themeBtn.textContent = t === "light" ? "☀" : "🌙";
      themeBtn.title = t === "light" ? "Switch to dark theme" : "Switch to light theme";
    }
  }
  let _theme = localStorage.getItem("comfyclaw_theme") || "dark";
  _applyTheme(_theme);
  themeBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    _theme = _theme === "light" ? "dark" : "light";
    localStorage.setItem("comfyclaw_theme", _theme);
    _applyTheme(_theme);
  });

  // ── Dock mode (sidebar default / float legacy) ──────────────────────────────
  const dockBtn = panel.querySelector("#comfyclaw-dock-btn");
  const closeBtn = panel.querySelector("#comfyclaw-close-btn");
  const header = panel.querySelector("#comfyclaw-gen-header");

  function _applyDock(mode) {
    panel.dataset.dock = mode;
    // Clear all positional inline styles first so CSS rules can take over.
    panel.style.left = "";
    panel.style.top = "";
    panel.style.right = "";
    panel.style.bottom = "";
    panel.style.width = "";
    panel.style.maxHeight = "";
    panel.style.height = "";
    panel.style.position = "";

    if (mode === "comfy-sidebar") {
      const ok = _attachPanelToSidebarHost();
      if (!ok) {
        // Host not ready — hide until render() fires.
        panel.style.display = "none";
      } else if (localStorage.getItem("comfyclaw_hidden") !== "1") {
        panel.style.display = "flex";
      }
      header.style.cursor = "default";
      // Ask ComfyUI to open our sidebar tab so the user actually sees the panel.
      try {
        const st = app?.extensionManager?.sidebarTab;
        if (st) {
          if (typeof st.activeSidebarTabId !== "undefined" && st.activeSidebarTabId !== "comfyclaw") {
            st.activeSidebarTabId = "comfyclaw";
          } else if (typeof st.toggleSidebarTab === "function" && !ok) {
            st.toggleSidebarTab("comfyclaw");
          }
        }
      } catch (_) {}
      if (dockBtn) { dockBtn.textContent = "⬒"; dockBtn.title = "Detach (cycle: sidebar → floating → right-rail)"; }
      return;
    }

    // Non-native modes: panel lives directly in document.body.
    _detachPanelToBody();
    panel.style.position = "fixed";

    if (mode === "sidebar") {
      panel.style.right = "0";
      const w = parseInt(localStorage.getItem("comfyclaw_sidebar_width") || "", 10);
      panel.style.width = (w >= 320 ? w : 400) + "px";
      header.style.cursor = "default";
      if (dockBtn) { dockBtn.textContent = "⌷"; dockBtn.title = "Cycle dock (right rail → floating → native)"; }
      return;
    }

    // Float mode — restore last saved position + size, or fall back.
    panel.style.right = "12px";
    panel.style.left = "auto";
    panel.style.top = "60px";
    panel.style.width = "400px";
    panel.style.maxHeight = "88vh";
    try {
      const saved = JSON.parse(localStorage.getItem("comfyclaw_panel_pos") || "null");
      if (saved?.left && saved?.top) {
        panel.style.right = "auto";
        panel.style.left = saved.left;
        panel.style.top  = saved.top;
      }
    } catch (_) {}
    header.style.cursor = "grab";
    if (dockBtn) { dockBtn.textContent = "⌸"; dockBtn.title = "Cycle dock (floating → native → right-rail)"; }
  }
  // Cycle order: comfy-sidebar (default) → float → sidebar → comfy-sidebar …
  const DOCK_ORDER = ["comfy-sidebar", "float", "sidebar"];
  let _dockMode = localStorage.getItem("comfyclaw_dock_mode") || "comfy-sidebar";
  if (!DOCK_ORDER.includes(_dockMode)) _dockMode = "comfy-sidebar";
  panel._reapplyDock = () => {
    _dockMode = localStorage.getItem("comfyclaw_dock_mode") || _dockMode;
    if (!DOCK_ORDER.includes(_dockMode)) _dockMode = "comfy-sidebar";
    _applyDock(_dockMode);
  };
  _applyDock(_dockMode);
  dockBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    const idx = DOCK_ORDER.indexOf(_dockMode);
    _dockMode = DOCK_ORDER[(idx + 1) % DOCK_ORDER.length];
    localStorage.setItem("comfyclaw_dock_mode", _dockMode);
    _applyDock(_dockMode);
    // Hint to the user — the icon is cryptic.
    showToast(`Dock: ${_dockMode === "comfy-sidebar" ? "ComfyUI sidebar" : _dockMode === "sidebar" ? "Right rail" : "Floating"}`, "info", 1600);
  });

  // ── Edge handle (paw tab on screen edge — reopens the panel) ───────────────
  let _edgeHandle = document.getElementById("comfyclaw-edge-handle");
  if (!_edgeHandle) {
    _edgeHandle = document.createElement("div");
    _edgeHandle.id = "comfyclaw-edge-handle";
    _edgeHandle.title = "Open ComfyClaw (⌘/Ctrl+Shift+Space)";
    _edgeHandle.textContent = "🐾";
    document.body.appendChild(_edgeHandle);
  }
  function _setPanelVisible(visible) {
    panel.style.display = visible ? "flex" : "none";
    _edgeHandle.style.display = visible ? "none" : "flex";
    if (visible) localStorage.removeItem("comfyclaw_hidden");
    else         localStorage.setItem("comfyclaw_hidden", "1");
  }
  _edgeHandle.addEventListener("click", () => _setPanelVisible(true));
  closeBtn?.addEventListener("click", (e) => { e.stopPropagation(); _setPanelVisible(false); });
  if (localStorage.getItem("comfyclaw_hidden") === "1") _setPanelVisible(false);

  // ── Drag (float mode only) ─────────────────────────────────────────────────
  let _dragState = null;
  header.addEventListener("mousedown", (e) => {
    if (_dockMode !== "float") return;
    if (e.target.closest("button")) return;
    if (e.button !== 0) return;
    const rect = panel.getBoundingClientRect();
    _dragState = { startX: e.clientX, startY: e.clientY,
                   offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top, dragging: false };
    e.preventDefault();
  });
  document.addEventListener("mousemove", (e) => {
    if (!_dragState) return;
    if (!_dragState.dragging && Math.hypot(e.clientX - _dragState.startX, e.clientY - _dragState.startY) < 5) return;
    _dragState.dragging = true;
    header.style.cursor = "grabbing";
    panel.style.right = "auto";
    panel.style.left = Math.max(0, e.clientX - _dragState.offsetX) + "px";
    panel.style.top  = Math.max(0, e.clientY - _dragState.offsetY) + "px";
  });
  document.addEventListener("mouseup", () => {
    if (!_dragState) return;
    if (_dragState.dragging) {
      localStorage.setItem("comfyclaw_panel_pos", JSON.stringify({ left: panel.style.left, top: panel.style.top }));
    }
    _dragState = null;
    header.style.cursor = _dockMode === "float" ? "grab" : "default";
  });

  // ── Persist global settings (non-session) ────────────────────────────────────
  ["comfyclaw-gen-verifier","comfyclaw-gen-vmodel",
   "comfyclaw-gen-apikey","comfyclaw-gen-opdelay","comfyclaw-gen-iters"].forEach(id => {
    const el = panel.querySelector(`#${id}`);
    if (!el) return;
    const stored = localStorage.getItem(id);
    if (stored !== null) el.value = stored;
    el.addEventListener("change", () => {
      localStorage.setItem(id, el.value);
      if (id === "comfyclaw-gen-opdelay") localStorage.setItem("comfyclaw_op_delay", el.value);
    });
  });
  // Debug-mode (dry-run) checkbox is persisted separately because it's a bool.
  const _dryEl = panel.querySelector("#comfyclaw-gen-dryrun");
  if (_dryEl) {
    _dryEl.checked = localStorage.getItem("comfyclaw-gen-dryrun") === "1";
    _dryEl.addEventListener("change", () => {
      localStorage.setItem("comfyclaw-gen-dryrun", _dryEl.checked ? "1" : "0");
    });
  }
  // Model is session-scoped — save on change
  panel.querySelector("#comfyclaw-gen-model")?.addEventListener("change", () => {
    const sess = _activeSession();
    sess.model = panel.querySelector("#comfyclaw-gen-model").value;
    _persistSessions();
  });
  // Prompt is session-scoped — save on change
  panel.querySelector("#comfyclaw-gen-prompt")?.addEventListener("input", () => {
    const sess = _activeSession();
    sess.prompt = panel.querySelector("#comfyclaw-gen-prompt").value;
    _persistSessions();
  });

  // ── Generate ──────────────────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-gen-btn").addEventListener("click", async () => {
    const promptEl = panel.querySelector("#comfyclaw-gen-prompt");
    let prompt = promptEl.value.trim();
    // If the controls are collapsed and the user is driving from the composer,
    // pull the composer text in as the generation prompt.
    if (!prompt) {
      const composerText = chatInput.value.trim();
      if (composerText) {
        promptEl.value = composerText;
        promptEl.dispatchEvent(new Event("input", { bubbles: true }));
        prompt = composerText;
        chatInput.value = "";
        chatInput.style.height = "auto";
      }
    }
    if (!prompt) {
      if (panel.querySelector("#comfyclaw-gen-body").style.display === "none") {
        chatInput.focus();
      } else {
        promptEl.focus();
      }
      return;
    }
    if (_activeSyncClient?.ws?.readyState !== WebSocket.OPEN) {
      // Loud-fail rather than silently swallowing the click — the most common
      // reason Generate appears to "do nothing" is that `comfyclaw serve`
      // isn't running and the WS is in CLOSED / CONNECTING state.
      const rs = _activeSyncClient?.ws?.readyState;
      const stateName = ({
        [WebSocket.CONNECTING]: "still connecting",
        [WebSocket.CLOSING]:    "closing",
        [WebSocket.CLOSED]:     "disconnected",
      })[rs] || "not connected";
      showToast(
        `ComfyClaw server is ${stateName}. ` +
        `Start it with \`comfyclaw serve\` in a terminal, then retry.`,
        "warning",
        6000,
      );
      setGenStatus("idle", "Backend not connected — start `comfyclaw serve`.");
      return;
    }
    let workflow = selectedMode === "improve" ? await exportCurrentWorkflow() : null;
    const cpWf = workflow || await exportCurrentWorkflow();
    if (cpWf && Object.keys(cpWf).length > 0)
      _activeSyncClient.ws.send(JSON.stringify({ type:"save_checkpoint", workflow:cpWf, label:`Before: ${prompt.slice(0,40)}` }));
    // Stamp the session with the current workflow identity at generation time
    const sess = _activeSession();
    if (sess) {
      const { name: wfId } = _detectWorkflowIdentity(workflow);
      if (!sess.workflowId) sess.workflowId = wfId;
      _persistSessions();
    }
    _captureCurrentSession();
    const _pp = _activeProvPayload();
    const runMode = _modeToggleRef?.value() || "auto";
    const agentBackend = _backendPickerRef?.value() || "litellm";
    const dryRun = !!panel.querySelector("#comfyclaw-gen-dryrun")?.checked;
    _activeSyncClient.ws.send(JSON.stringify({
      type:          "trigger_generation",
      connection_id: _CONNECTION_ID,
      prompt, mode: selectedMode, workflow,
      settings: {
        iterations:     runMode === "manual" ? 1 :
                        (parseInt(panel.querySelector("#comfyclaw-gen-iters").value) || 3),
        mode:           runMode,
        run_mode:       runMode,
        verifier_mode:  panel.querySelector("#comfyclaw-gen-verifier").value,
        model:          panel.querySelector("#comfyclaw-gen-model").value,
        verifier_model: panel.querySelector("#comfyclaw-gen-vmodel").value,
        agent_backend:  agentBackend,
        api_key:        _pp.api_key  || panel.querySelector("#comfyclaw-gen-apikey").value.trim() || undefined,
        api_base:       _pp.api_base || undefined,
        dry_run:        dryRun,
      },
    }));
    _lastGenState = null;
    setGenRunning(true);
    setGenStatus(
      "running",
      dryRun ? `Debug mode (${runMode}) — building workflow only…`
             : `Waiting for agent (${runMode})…`
    );
    clearAgentLog();
    if (_historyTabRef) {
      _historyTabRef.startRun({ prompt, mode: dryRun ? `${runMode} · debug` : runMode });
    }
  });

  // ── Stop ──────────────────────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-gen-stop").addEventListener("click", () => {
    if (_activeSyncClient?.ws?.readyState === WebSocket.OPEN)
      _activeSyncClient.ws.send(JSON.stringify({ type: "cancel_generation" }));
    setGenRunning(false); setGenStatus("idle", "Cancelled.");
  });

  // ── Debug ─────────────────────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-debug-btn").addEventListener("click", async () => {
    if (_activeSyncClient?.ws?.readyState !== WebSocket.OPEN) return;
    const workflow = await exportCurrentWorkflow();
    const _dpp = _activeProvPayload();
    _activeSyncClient.ws.send(JSON.stringify({
      type:     "debug_workflow", workflow,
      model:    panel.querySelector("#comfyclaw-gen-model").value.trim() || undefined,
      api_key:  _dpp.api_key  || panel.querySelector("#comfyclaw-gen-apikey").value.trim() || undefined,
      api_base: _dpp.api_base || undefined,
    }));
    setGenStatus("verifying", "Running debug agent…");
    panel.querySelector("#comfyclaw-gen-status").style.display = "block";
  });

  // ── Manual checkpoint save ────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-cp-save-btn").addEventListener("click", async () => {
    if (_activeSyncClient?.ws?.readyState !== WebSocket.OPEN) return;
    const wf = await exportCurrentWorkflow();
    _activeSyncClient.ws.send(JSON.stringify({ type:"save_checkpoint", workflow:wf,
      label:`Manual — ${new Date().toLocaleTimeString()}` }));
  });

  // ── Chat input (dual-mode: chat when idle, refinement when generating) ────────
  const chatInput = panel.querySelector("#comfyclaw-think-input");
  const chatSend  = panel.querySelector("#comfyclaw-think-send");

  function sendFromPanel() {
    const text = chatInput.value.trim();
    if (!text || _chatStreaming) return;
    if (_activeSyncClient?.ws?.readyState !== WebSocket.OPEN) return;
    appendAgentLog({ event_type: "user", content: text, timestamp: Date.now() / 1000 });
    chatInput.value = "";
    // Auto-resize back
    chatInput.style.height = "auto";
    if (_isGenerating) {
      _activeSyncClient.ws.send(JSON.stringify({ type: "user_refinement", text }));
    } else {
      _chatHistory.push({ role: "user", content: text });
      const msgId = `chat_${++_chatMsgIdSeq}`;
      _chatStreaming = true; chatSend.disabled = true;
      _thinkingChatMsgId = msgId;
      appendAgentLog({ event_type: "assistant_stream", content: "", timestamp: Date.now() / 1000, message_id: msgId });
      const _cpp = _activeProvPayload();
      _activeSyncClient.ws.send(JSON.stringify({
        type:          "chat_message",
        message_id:    msgId,
        messages:      _chatHistory,
        model:         panel.querySelector("#comfyclaw-gen-model").value.trim() || undefined,
        api_key:       _cpp.api_key  || panel.querySelector("#comfyclaw-gen-apikey").value.trim() || undefined,
        api_base:      _cpp.api_base || undefined,
        agent_backend: _activeBackendId(),
      }));
    }
  }

  chatSend.addEventListener("click", sendFromPanel);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendFromPanel(); }
  });
  // Auto-grow textarea
  chatInput.addEventListener("input", () => {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + "px";
  });

  // ── Composer model chip + popover (Cursor-style inline model picker) ───────
  const modelChip      = panel.querySelector("#cc-composer-model-chip");
  const modelChipLabel = modelChip?.querySelector(".cc-chip-label");
  const modelChipDot   = modelChip?.querySelector(".cc-chip-dot");
  const modelSelectEl  = panel.querySelector("#comfyclaw-gen-model");
  const providerStateEl = panel.querySelector("#comfyclaw-provider-state");

  function _currentModelLabel() {
    const v = modelSelectEl?.value || "";
    if (!v) return "Server default";
    // Find the matching label in PROVIDERS.
    for (const prov of Object.values(PROVIDERS)) {
      const m = prov.models.find((x) => x.value === v);
      if (m) return m.label;
    }
    return v;
  }
  function _refreshModelChip() {
    if (!modelChip) return;
    if (modelChipLabel) modelChipLabel.textContent = _currentModelLabel();
    if (modelChipDot) {
      const provKey = providerStateEl?.dataset?.provider || "anthropic";
      const c = PROVIDERS[provKey]?.color;
      modelChipDot.style.background = c || "var(--cc-accent)";
    }
  }

  let _modelPopover = null;
  function _openModelPopover() {
    if (!modelChip) return;
    if (!_modelPopover) {
      _modelPopover = document.createElement("div");
      _modelPopover.className = "cc-popover";
      document.body.appendChild(_modelPopover);
      // Close on outside click.
      document.addEventListener("mousedown", (e) => {
        if (_modelPopover?.dataset?.open !== "1") return;
        if (_modelPopover.contains(e.target) || modelChip.contains(e.target)) return;
        _modelPopover.dataset.open = "0";
      });
    }
    // Rebuild contents fresh.
    const curVal = modelSelectEl?.value || "";
    let html = `<div class="cc-popover-item" data-val="" ${curVal === "" ? 'data-active="1"' : ""}>
                  <span class="cc-popover-icon">·</span>
                  <span>Server default</span>
                </div>`;
    for (const [key, prov] of Object.entries(PROVIDERS)) {
      html += `<div class="cc-popover-section-label" style="color:${prov.color};">
                 ${prov.emoji} ${prov.label}
               </div>`;
      for (const m of prov.models) {
        const active = m.value === curVal;
        html += `<div class="cc-popover-item" data-val="${m.value}" data-prov="${key}"${active ? ' data-active="1"' : ""}>
                   <span class="cc-popover-icon">${active ? "✓" : ""}</span>
                   <span>${m.label}</span>
                 </div>`;
      }
    }
    _modelPopover.innerHTML = html;
    // Position near the chip.
    const r = modelChip.getBoundingClientRect();
    _modelPopover.style.left = `${Math.max(8, r.left)}px`;
    // Show ABOVE the chip (composer is at the bottom).
    _modelPopover.style.top = `${r.top - 8}px`;
    _modelPopover.style.transform = "translateY(-100%)";
    _modelPopover.dataset.open = "1";
    // Wire item clicks.
    _modelPopover.querySelectorAll(".cc-popover-item").forEach((item) => {
      item.addEventListener("click", () => {
        const v = item.dataset.val ?? "";
        const provKey = item.dataset.prov;
        if (provKey) _setActiveProvider(provKey);
        if (modelSelectEl) {
          // Ensure the option exists (provider switch repopulates the select).
          if (!modelSelectEl.querySelector(`[value="${v}"]`) && v) {
            const opt = document.createElement("option");
            opt.value = v; opt.textContent = v;
            modelSelectEl.appendChild(opt);
          }
          modelSelectEl.value = v;
          modelSelectEl.dispatchEvent(new Event("change", { bubbles: true }));
        }
        _refreshModelChip();
        _modelPopover.dataset.open = "0";
      });
    });
  }
  modelChip?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (_modelPopover?.dataset?.open === "1") _modelPopover.dataset.open = "0";
    else _openModelPopover();
  });
  modelSelectEl?.addEventListener("change", _refreshModelChip);
  _refreshModelChip();
  // Refresh after provider changes too (provider buttons swap the dropdown).
  panel.querySelectorAll(".cc-provider-btn").forEach((b) =>
    b.addEventListener("click", () => setTimeout(_refreshModelChip, 0)));

  // ── Composer backend chip + popover (LiteLLM / Claude Code / Codex / …) ───
  const beChip      = panel.querySelector("#cc-composer-backend-chip");
  const beChipLabel = beChip?.querySelector(".cc-chip-label");
  const beChipIcon  = beChip?.querySelector(".cc-chip-icon");

  const BACKEND_META = {
    "litellm":     { label: "LiteLLM",     icon: "⚙", needsApiKey: true  },
    "claude-code": { label: "Claude Code", icon: "◆", needsApiKey: false },
    "codex":       { label: "Codex",       icon: "◇", needsApiKey: false },
    "gemini-cli":  { label: "Gemini CLI",  icon: "✦", needsApiKey: false },
  };
  function _activeBackendId() {
    return _backendPickerRef?.value() || localStorage.getItem("comfyclaw_agent_backend") || "litellm";
  }
  function _refreshBackendChip() {
    if (!beChip) return;
    const id   = _activeBackendId();
    const meta = BACKEND_META[id] || BACKEND_META["litellm"];
    if (beChipLabel) beChipLabel.textContent = meta.label;
    if (beChipIcon)  beChipIcon.textContent  = meta.icon;
    // CLI backends manage their own model — visually de-emphasize the model
    // chip so the user isn't confused into thinking it controls them.
    const modelChipEl = panel.querySelector("#cc-composer-model-chip");
    if (modelChipEl) {
      modelChipEl.style.display = meta.needsApiKey ? "" : "none";
    }
  }

  let _bePopover = null;

  // Color-coded mini-dot for the popover rows; mirrors the picker's logic.
  function _stateDot(state) {
    let color = "var(--cc-fg-faint)";
    if (state === "ok") color = "var(--cc-accent-green)";
    else if (state === "needs_auth") color = "var(--cc-accent, #f0a500)";
    else if (state === "needs_install" || state === "error") color = "var(--cc-accent-red)";
    return `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;
                          background:${color};margin-right:6px;"></span>`;
  }

  function _stateLabel(state) {
    if (state === "needs_install") return "Not installed";
    if (state === "needs_auth")    return "Not signed in";
    if (state === "unsupported")   return "Unavailable";
    if (state === "error")         return "Error";
    return "";
  }

  function _openBackendPopover() {
    if (!beChip) return;
    if (!_bePopover) {
      _bePopover = document.createElement("div");
      _bePopover.className = "cc-popover";
      document.body.appendChild(_bePopover);
      document.addEventListener("mousedown", (e) => {
        if (_bePopover?.dataset?.open !== "1") return;
        if (_bePopover.contains(e.target) || beChip.contains(e.target)) return;
        _bePopover.dataset.open = "0";
      });
    }
    const curId = _activeBackendId();
    let html = `<div class="cc-popover-section-label">Agent backend</div>`;
    for (const [id, meta] of Object.entries(BACKEND_META)) {
      const active = id === curId;
      const st = _backendPickerRef?.status?.(id) || null;
      const state = st?.state || "ok";
      const detail = st?.detail || meta.label;
      const isUsable = state === "ok";
      const subLabel = _stateLabel(state);

      let actionBtn = "";
      if (state === "needs_install" && st?.can_install) {
        actionBtn = `<button class="cc-popover-action" data-action="install" data-be="${id}"
                              style="margin-left:auto;font-size:10px;padding:2px 8px;
                                     background:var(--cc-accent);color:var(--cc-bg);
                                     border:none;border-radius:6px;cursor:pointer;">
                       Install
                     </button>`;
      } else if (state === "needs_auth") {
        actionBtn = `<button class="cc-popover-action" data-action="signin" data-be="${id}"
                              style="margin-left:auto;font-size:10px;padding:2px 8px;
                                     background:var(--cc-accent);color:var(--cc-bg);
                                     border:none;border-radius:6px;cursor:pointer;">
                       Sign in
                     </button>`;
      }

      const subline = subLabel
        ? `<div style="font-size:10px;color:var(--cc-fg-muted);margin-top:1px;">
             ${_stateDot(state)}${subLabel}
           </div>`
        : "";

      html += `<div class="cc-popover-item${isUsable ? "" : " cc-popover-item-locked"}"
                    data-be="${id}"${active ? ' data-active="1"' : ""}
                    title="${detail.replace(/"/g, "&quot;")}"
                    style="display:flex;align-items:center;gap:8px;padding:6px 10px;">
                 <span class="cc-popover-icon">${active ? "✓" : meta.icon}</span>
                 <span style="flex:1;min-width:0;">
                   <div>${meta.label}</div>
                   ${subline}
                 </span>
                 ${actionBtn}
               </div>`;
    }
    _bePopover.innerHTML = html;
    const r = beChip.getBoundingClientRect();
    _bePopover.style.left = `${Math.max(8, r.left)}px`;
    _bePopover.style.top = `${r.top - 8}px`;
    _bePopover.style.transform = "translateY(-100%)";
    _bePopover.dataset.open = "1";

    // Action buttons (Install / Sign in) take priority and stopPropagation
    // so they don't also trigger the row's select-this-backend click.
    _bePopover.querySelectorAll(".cc-popover-action").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = btn.dataset.be;
        const action = btn.dataset.action;
        _bePopover.dataset.open = "0";
        if (action === "install") _openClaudeInstallModal(id);
        else if (action === "signin") _openClaudeAuthModal(id);
      });
    });

    _bePopover.querySelectorAll(".cc-popover-item").forEach((item) => {
      item.addEventListener("click", (e) => {
        if (e.target.closest(".cc-popover-action")) return;
        const id = item.dataset.be;
        const st = _backendPickerRef?.status?.(id) || null;
        if (st && st.state && st.state !== "ok") {
          // Don't activate an unusable backend; surface a hint instead.
          showToast(
            st.state === "needs_install"
              ? "Install Claude Code first."
              : st.state === "needs_auth"
                ? "Sign in to Claude Code first."
                : "This backend is unavailable.",
            "warning",
            2200,
          );
          return;
        }
        if (_backendPickerRef?.set) _backendPickerRef.set(id);
        else localStorage.setItem("comfyclaw_agent_backend", id);
        _refreshBackendChip();
        _bePopover.dataset.open = "0";
      });
    });
  }

  function _openClaudeInstallModal(backendId) {
    if (backendId !== "claude-code") return;
    if (_installModal?.isOpen?.()) return;

    const logEl = document.createElement("pre");
    logEl.style.cssText = `
      margin:0;padding:10px;background:var(--cc-surface-tint);
      border:1px solid var(--cc-border);border-radius:6px;
      font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;
      line-height:1.5;max-height:300px;overflow:auto;white-space:pre-wrap;
      word-break:break-all;color:var(--cc-fg);
    `;
    const statusEl = document.createElement("div");
    statusEl.style.cssText = "font-size:11px;color:var(--cc-fg-muted);margin-top:10px;";
    statusEl.textContent = "Starting installer…";

    const explainer = document.createElement("div");
    explainer.style.cssText = "font-size:11px;color:var(--cc-fg-muted);margin-bottom:8px;line-height:1.5;";
    explainer.innerHTML = `
      This will install the Claude Code CLI by running the official installer.
      The installer is bundled by Anthropic; no other command will be executed.
    `;

    const container = document.createElement("div");
    container.appendChild(explainer);
    container.appendChild(logEl);
    container.appendChild(statusEl);

    _installModal = openModal({
      title: "Install Claude Code",
      subtitle: "Streaming installer output",
      body: container,
      width: 640,
      onClose: () => {
        if (_installModal?._inFlight) {
          _wsSend({ type: "backend_install_cancel", backend: backendId });
        }
        _installModal = null;
      },
    });
    _installModal._inFlight = true;
    _installModal._appendLine = (level, text) => {
      const line = document.createElement("div");
      if (level === "error")       line.style.color = "var(--cc-accent-red)";
      else if (level === "info")   line.style.color = "var(--cc-fg-muted)";
      line.textContent = text;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    };
    _installModal._setStatus = (text, kind) => {
      statusEl.textContent = text;
      statusEl.style.color =
        kind === "error" ? "var(--cc-accent-red)" :
        kind === "ok"    ? "var(--cc-accent-green)" :
        "var(--cc-fg-muted)";
    };

    if (!_wsSend({ type: "backend_install_start", backend: backendId })) {
      _installModal._setStatus("Sync server is not connected.", "error");
      _installModal._inFlight = false;
    }
  }

  function _openClaudeAuthModal(backendId) {
    if (backendId !== "claude-code") return;
    if (_authModal?.isOpen?.()) return;

    const stepEl = document.createElement("div");
    stepEl.style.cssText = "font-size:12px;line-height:1.6;color:var(--cc-fg);";
    const statusEl = document.createElement("div");
    statusEl.style.cssText = "font-size:11px;color:var(--cc-fg-muted);margin-top:14px;";
    statusEl.textContent = "Starting sign-in…";

    const container = document.createElement("div");
    container.appendChild(stepEl);
    container.appendChild(statusEl);

    _authModal = openModal({
      title: "Sign in to Claude",
      subtitle: "Browser-based sign-in (no terminal needed)",
      body: container,
      width: 560,
      onClose: () => {
        if (_authModal?._inFlight) {
          _wsSend({ type: "backend_auth_cancel", backend: backendId });
        }
        _authModal = null;
      },
    });
    _authModal._inFlight = true;
    _authModal._backendId = backendId;

    _authModal._showWaiting = () => {
      stepEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;">
          <span class="cc-spinner" style="
            width:14px;height:14px;border:2px solid var(--cc-border);
            border-top-color:var(--cc-accent);border-radius:50%;
            animation:cc-spin 0.8s linear infinite;"></span>
          <span>Asking Claude for a sign-in link…</span>
        </div>
      `;
    };

    _authModal._showSignInLink = (url) => {
      _authModal._oauthUrl = url;
      stepEl.innerHTML = `
        <div style="margin-bottom:14px;">
          <strong>Step 1 — Sign in</strong>
          <div style="font-size:11px;color:var(--cc-fg-muted);margin-top:4px;">
            Click the button below. It opens Claude.ai in a new tab where you can sign in.
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px;">
          <a class="cc-auth-open-link" href="${url}" target="_blank" rel="noopener"
             style="background:var(--cc-accent);color:var(--cc-bg);padding:8px 14px;
                    border-radius:8px;text-decoration:none;font-weight:600;
                    font-size:12px;display:inline-flex;align-items:center;gap:6px;">
            Open Claude sign-in &rarr;
          </a>
          <button class="cc-auth-copy-link" style="background:transparent;
                  border:1px solid var(--cc-border);color:var(--cc-fg);
                  padding:8px 14px;border-radius:8px;font-size:12px;cursor:pointer;">
            Copy URL
          </button>
        </div>
        <div style="margin-bottom:8px;">
          <strong>Step 2 — Paste the redirect URL</strong>
          <div style="font-size:11px;color:var(--cc-fg-muted);margin-top:4px;line-height:1.5;">
            After signing in, your browser will show an error page (that's expected on a
            remote server). <strong>Copy the URL from your browser's address bar</strong>
            and paste it below.
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:stretch;">
          <input class="cc-auth-paste" type="text"
                 placeholder="https://...?code=..."
                 spellcheck="false" autocomplete="off"
                 style="flex:1;padding:8px 10px;background:var(--cc-surface-tint);
                        color:var(--cc-fg);border:1px solid var(--cc-border);
                        border-radius:8px;font-family:ui-monospace,Menlo,Consolas,monospace;
                        font-size:11px;">
          <button class="cc-auth-submit"
                  style="background:var(--cc-accent);color:var(--cc-bg);
                         border:none;border-radius:8px;padding:8px 16px;
                         font-weight:600;font-size:12px;cursor:pointer;">
            Submit
          </button>
        </div>
      `;
      const $copy   = stepEl.querySelector(".cc-auth-copy-link");
      const $input  = stepEl.querySelector(".cc-auth-paste");
      const $submit = stepEl.querySelector(".cc-auth-submit");
      $copy?.addEventListener("click", () => {
        navigator.clipboard?.writeText(url).then(
          () => showToast("URL copied", "success", 1500),
          () => showToast("Could not access clipboard", "error", 2000),
        );
      });
      const submit = () => {
        const v = $input.value.trim();
        if (!v) { $input.focus(); return; }
        _wsSend({ type: "backend_auth_paste_code", backend: backendId, url: v });
        _authModal._setStatus("Submitting redirect URL…", "info");
      };
      $submit?.addEventListener("click", submit);
      $input?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); submit(); }
      });
      setTimeout(() => $input?.focus(), 50);
    };

    _authModal._setStatus = (text, kind) => {
      statusEl.textContent = text;
      statusEl.style.color =
        kind === "error" ? "var(--cc-accent-red)" :
        kind === "ok"    ? "var(--cc-accent-green)" :
        "var(--cc-fg-muted)";
    };

    _authModal._showSuccess = (detail) => {
      stepEl.innerHTML = `
        <div style="display:flex;align-items:center;gap:10px;color:var(--cc-accent-green);">
          <span style="font-size:18px;">✓</span>
          <div>
            <div style="font-weight:600;">Signed in to Claude</div>
            <div style="font-size:11px;color:var(--cc-fg-muted);margin-top:2px;">
              ${escHtml(detail || "")}
            </div>
          </div>
        </div>
      `;
      _authModal._setStatus("You can close this and start generating.", "ok");
    };

    _authModal._showFailure = (detail) => {
      stepEl.innerHTML = `
        <div style="display:flex;align-items:flex-start;gap:10px;color:var(--cc-accent-red);">
          <span style="font-size:18px;">✕</span>
          <div>
            <div style="font-weight:600;">Sign-in failed</div>
            <div style="font-size:11px;color:var(--cc-fg-muted);margin-top:4px;
                        line-height:1.5;word-break:break-word;">
              ${escHtml(detail || "")}
            </div>
            <button class="cc-auth-retry"
                    style="margin-top:10px;background:var(--cc-accent);color:var(--cc-bg);
                           border:none;border-radius:6px;padding:6px 14px;font-size:12px;
                           font-weight:600;cursor:pointer;">
              Try again
            </button>
          </div>
        </div>
      `;
      stepEl.querySelector(".cc-auth-retry")?.addEventListener("click", () => {
        _authModal?.close?.();
        setTimeout(() => _openClaudeAuthModal(backendId), 150);
      });
    };

    _authModal._showWaiting();
    if (!_wsSend({ type: "backend_auth_start", backend: backendId, auth_method: "claudeai" })) {
      _authModal._setStatus("Sync server is not connected.", "error");
      _authModal._inFlight = false;
    }
  }

  // Expose for use from the picker's action callback (it isn't in scope here
  // when createBackendPicker is called below, so we cache them on the panel
  // element via a custom event).
  panel.addEventListener("cc-backend-action", (e) => {
    const { id, action } = e.detail || {};
    if (action === "install") _openClaudeInstallModal(id);
    else if (action === "signin") _openClaudeAuthModal(id);
  });
  beChip?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (_bePopover?.dataset?.open === "1") _bePopover.dataset.open = "0";
    else _openBackendPopover();
  });
  // Custom event so the legacy picker (or any other code path) can poke us.
  beChip?.addEventListener("cc-backend-refresh", _refreshBackendChip);
  // Refresh after the legacy picker finishes its initial load (server tells us
  // backend availability via WS, which can demote the saved backend to litellm).
  setTimeout(_refreshBackendChip, 0);
  setTimeout(_refreshBackendChip, 1000);

  // ── Composer Run button (uses chat input as generation prompt) ─────────────
  const composerRun = panel.querySelector("#cc-composer-run");
  composerRun?.addEventListener("click", () => {
    const text = chatInput.value.trim();
    if (!text) { chatInput.focus(); return; }
    const gp = panel.querySelector("#comfyclaw-gen-prompt");
    if (gp) {
      gp.value = text;
      gp.dispatchEvent(new Event("input", { bubbles: true }));
    }
    chatInput.value = "";
    chatInput.style.height = "auto";
    panel.querySelector("#comfyclaw-gen-btn")?.click();
  });

  // ── Composer Stop button (cancels in-flight generation) ────────────────────
  panel.querySelector("#cc-composer-stop")?.addEventListener("click", () => {
    panel.querySelector("#comfyclaw-gen-stop")?.click();
  });

  // ── Composer Audit button (debug the current workflow) ─────────────────────
  panel.querySelector("#cc-composer-audit")?.addEventListener("click", () => {
    panel.querySelector("#comfyclaw-debug-btn")?.click();
  });

  // ── Composer Strategy chip — cycles Scratch / Improve, mirrors hidden btns ─
  const stratChip = panel.querySelector("#cc-composer-strategy-chip");
  const stratLabel = stratChip?.querySelector(".cc-chip-label");
  const stratIcon  = stratChip?.querySelector(".cc-chip-icon");
  function _refreshStrategyChip() {
    const mode = stratChip?.dataset.mode || "scratch";
    if (stratIcon)  stratIcon.textContent  = mode === "improve" ? "🔧" : "✨";
    if (stratLabel) stratLabel.textContent = mode === "improve" ? "Improve" : "Scratch";
  }
  if (stratChip) {
    stratChip.dataset.mode = "scratch";
    stratChip.addEventListener("click", () => {
      const next = stratChip.dataset.mode === "scratch" ? "improve" : "scratch";
      stratChip.dataset.mode = next;
      // Drive the hidden mode buttons that the existing flow reads.
      panel.querySelector(`#comfyclaw-gen-mode [data-mode="${next}"]`)?.click();
      _refreshStrategyChip();
    });
    _refreshStrategyChip();
  }

  // ── Textarea focus highlight (uses CSS via .cc-textarea/.cc-input) ────────
  // The `:focus` styles in styles.js already raise a soft purple ring; no
  // imperative JS needed beyond the default behaviour.

  // ── Resize handles (bottom-right for float, left edge for sidebar) ──────────
  const resizeHandle = document.createElement("div");
  resizeHandle.className = "cc-panel-resize-corner";
  resizeHandle.style.cssText = `
    position:absolute; bottom:0; right:0; width:14px; height:14px;
    cursor:se-resize; z-index:2;
    background:linear-gradient(135deg, transparent 60%, var(--cc-border) 60%);
    border-radius:0 0 var(--cc-radius) 0;
    transition: opacity 0.15s;
  `;
  resizeHandle.addEventListener("mouseenter", () => resizeHandle.style.opacity = "0.7");
  resizeHandle.addEventListener("mouseleave", () => resizeHandle.style.opacity = "1");
  panel.style.position = "fixed";
  panel.appendChild(resizeHandle);

  const edgeResize = document.createElement("div");
  edgeResize.className = "cc-panel-resize-edge";
  panel.appendChild(edgeResize);

  let _resizing = null;   // { mode: "corner" | "edge", startX, startY, w, h }
  resizeHandle.addEventListener("mousedown", e => {
    if (_dockMode !== "float") return;
    e.preventDefault(); e.stopPropagation();
    _resizing = { mode: "corner", x: e.clientX, y: e.clientY,
                  w: panel.offsetWidth, h: panel.offsetHeight };
  });
  edgeResize.addEventListener("mousedown", e => {
    if (_dockMode !== "sidebar") return;
    e.preventDefault(); e.stopPropagation();
    _resizing = { mode: "edge", x: e.clientX, w: panel.offsetWidth };
  });
  document.addEventListener("mousemove", e => {
    if (!_resizing) return;
    if (_resizing.mode === "corner") {
      const newW = Math.max(320, _resizing.w + (e.clientX - _resizing.x));
      const newH = Math.max(300, Math.min(window.innerHeight * 0.92, _resizing.h + (e.clientY - _resizing.y)));
      panel.style.width    = newW + "px";
      panel.style.maxHeight = newH + "px";
    } else {
      // Sidebar: dragging the left edge to the LEFT grows the panel.
      const newW = Math.max(320, Math.min(window.innerWidth - 80, _resizing.w - (e.clientX - _resizing.x)));
      panel.style.width = newW + "px";
    }
  });
  document.addEventListener("mouseup", () => {
    if (_resizing?.mode === "edge") {
      localStorage.setItem("comfyclaw_sidebar_width", String(parseInt(panel.style.width)));
    }
    _resizing = null;
  });

  // ── Global keyboard shortcut: Ctrl/Cmd+Shift+Space to toggle panel ────────────
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.code === "Space") {
      e.preventDefault();
      _setPanelVisible(panel.style.display === "none");
    }
  });

  // ── Phase 4: tabbed shell + new feature widgets ────────────────────────────
  _augmentPanelWithTabs(panel);

  return panel;
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase 4 augmentation: wrap legacy controls in a Generate tab and add Skills,
// History tabs.  Also injects the Manual/Auto/Co-pilot mode toggle and the
// agent-backend picker into the Generate tab header.
// ─────────────────────────────────────────────────────────────────────────────

let _scoreboardSink = null;   // (msg) => void; bound by tab augmentation
let _historyTabRef  = null;   // .startRun / .endRun / .addImage / .addIterationScore
let _skillsTabRef   = null;
let _modeToggleRef  = null;
let _backendPickerRef = null;
let _tabStripRef    = null;   // .setBadge / .activate / .value
let _lastGenState   = null;   // "done" | "dry_run" | "error" — for History.endRun

function _augmentPanelWithTabs(panel) {
  const headerEl = panel.querySelector("#comfyclaw-gen-header");
  const generateBody = panel.querySelector("#comfyclaw-gen-body");
  const actionBar    = panel.querySelector("#comfyclaw-action-bar");
  const logBody      = panel.querySelector("#comfyclaw-think-body");
  if (!headerEl || !generateBody || !logBody) return;

  // 1) Build the skills + history tabs (lazy).
  const skillsTab  = createSkillsTab({ getWs: () => _activeSyncClient?.ws });
  const historyTab = createHistoryTab({
    onReusePrompt: (text) => {
      if (!text) return;
      // Drop the prompt back into the Generate tab textarea, switch to it,
      // and focus the field so the user can edit + click Generate.
      const ta = panel.querySelector("#comfyclaw-gen-prompt");
      if (ta) {
        ta.value = text;
        ta.dispatchEvent(new Event("input", { bubbles: true }));
      }
      _tabStripRef?.activate("generate");
      ta?.focus();
      showToast("Prompt loaded into Generate", "info", 1800);
    },
  });
  _skillsTabRef  = skillsTab;
  _historyTabRef = historyTab;

  // Wrap the legacy generate body + log body into one Generate slot.
  //
  // The slot is a flex column with `overflow:hidden` so the inner
  // #comfyclaw-think-log scrolls instead of overflowing out of the panel.
  // We keep a real `min-height` on the slot (NOT 0) so that, in the absence
  // of a fixed panel height (the panel only has max-height:88vh), the slot
  // still claims usable space — otherwise `flex: 1 1 0` collapses to zero
  // and the panel ends up showing only the tab strip.
  const SLOT_CSS =
    "display:flex;flex-direction:column;flex:1 1 auto;min-height:360px;" +
    "overflow:hidden;";

  const generateSlot = document.createElement("div");
  generateSlot.style.cssText = SLOT_CSS;
  // Move existing children into the slot.
  const parent = generateBody.parentElement;
  parent.insertBefore(generateSlot, generateBody);
  generateSlot.appendChild(generateBody);
  if (actionBar) generateSlot.appendChild(actionBar);
  generateSlot.appendChild(logBody);

  // Wrap skills + history slots — same flex+overflow recipe.
  const skillsSlot = document.createElement("div");
  skillsSlot.style.cssText = SLOT_CSS;
  skillsSlot.appendChild(skillsTab.root);

  const historySlot = document.createElement("div");
  historySlot.style.cssText = SLOT_CSS;
  historySlot.appendChild(historyTab.root);

  parent.appendChild(skillsSlot);
  parent.appendChild(historySlot);

  // 2) Build tab strip.
  const tabStrip = createTabStrip({
    initial: "generate",
    tabs: [
      { id: "generate", label: "Generate", icon: "✨",
        title: "Compose prompts and run the agent" },
      { id: "skills",   label: "Skills",   icon: "📚",
        title: "Browse, import, and manage skills",
        onActivate: () => skillsTab.refresh() },
      { id: "history",  label: "History",  icon: "🖼",
        title: "Past generations with iteration scores and image previews" },
    ],
  });
  _tabStripRef = tabStrip;
  // Insert tab strip just below the header.
  parent.insertBefore(tabStrip.root, generateSlot);
  tabStrip.bindSlot("generate", generateSlot);
  tabStrip.bindSlot("skills",   skillsSlot);
  tabStrip.bindSlot("history",  historySlot);

  // 3) Inject the Mode toggle (Manual/Auto/Co-pilot) at the top of the
  // controls body — strategy buttons are hidden now (the composer drives
  // strategy), so this is the first visible knob.
  const legacyModeRow = panel.querySelector("#comfyclaw-gen-mode");
  if (legacyModeRow) {
    const _applyModeToAdvanced = (m) => {
      const itEl  = panel.querySelector("#comfyclaw-gen-iters");
      const verEl = panel.querySelector("#comfyclaw-gen-verifier");
      const advDetails = panel.querySelector("#comfyclaw-adv-details");
      const modePill   = panel.querySelector("#comfyclaw-adv-mode-pill");
      const manualHint = panel.querySelector("#comfyclaw-adv-manual-hint");
      // 1) Coerce values to match the mode's semantics.
      if (m === "manual") {
        if (itEl && parseInt(itEl.value) !== 1) {
          itEl.value = 1;
          localStorage.setItem("comfyclaw-gen-iters", "1");
        }
      } else {
        // Auto / Co-pilot: bump iterations back up if user left it at 1.
        if (itEl && parseInt(itEl.value) <= 1) {
          itEl.value = 3;
          localStorage.setItem("comfyclaw-gen-iters", "3");
        }
        if (verEl) {
          // Switching INTO copilot/auto sets a sensible default if the
          // current verifier mismatches the spirit of the mode.
          if (m === "auto" && verEl.value === "human") {
            verEl.value = "vlm";
            localStorage.setItem("comfyclaw-gen-verifier", "vlm");
          } else if (m === "copilot" && verEl.value === "vlm") {
            verEl.value = "human";
            localStorage.setItem("comfyclaw-gen-verifier", "human");
          }
        }
      }
      // 2) Hide rows whose data-modes don't include the active mode.
      panel.querySelectorAll(".cc-adv-row").forEach((row) => {
        const allowed = (row.dataset.modes || "").split(",").map((s) => s.trim());
        row.style.display = allowed.includes(m) ? "" : "none";
      });
      // 3) In Manual: show the hint and auto-collapse the Advanced section
      //    (since most knobs are hidden, the disclosure has little payoff).
      if (manualHint) manualHint.style.display = m === "manual" ? "block" : "none";
      if (advDetails && m === "manual" && advDetails.open) advDetails.open = false;
      // 4) Surface the active mode on the Advanced summary so users see the
      //    relationship without expanding.
      if (modePill) {
        modePill.textContent = m;
        const colorMap = {
          manual:  "var(--cc-fg-dim)",
          auto:    "var(--cc-accent-blue)",
          copilot: "var(--cc-accent-orange)",
        };
        modePill.style.color = colorMap[m] || "var(--cc-fg-dim)";
        modePill.style.borderColor = colorMap[m] || "var(--cc-border)";
      }
    };

    const modeToggle = createModeToggle({
      onChange: _applyModeToAdvanced,
    });
    _modeToggleRef = modeToggle;
    legacyModeRow.parentElement.insertBefore(modeToggle.root, legacyModeRow);
    // Apply once at startup so the saved mode's relationship to Advanced is
    // reflected before the user touches anything. Defer one tick so the
    // markup the function reads is fully wired.
    setTimeout(() => _applyModeToAdvanced(modeToggle.value()), 0);
  }

  // 4) Backend picker — composer chip is the single UI now. The legacy
  // picker stays in DOM (hidden) so its value/setAvailability hooks keep
  // working with the WS protocol.
  const provBar = panel.querySelector("#comfyclaw-provider-bar");
  if (provBar) {
    const picker = createBackendPicker({
      onChange: () => {
        // When backend changes through any path, refresh the composer chip.
        const chip = panel.querySelector("#cc-composer-backend-chip");
        if (chip) chip.dispatchEvent(new CustomEvent("cc-backend-refresh"));
      },
      onAction: (id, action) => {
        // The picker fires this when an Install/Sign-in affordance is clicked
        // from any UI surface that uses the picker. We re-dispatch into the
        // panel's namespace so the modal handlers (closures over `panel`) can
        // pick it up regardless of which UI the click originated from.
        panel.dispatchEvent(
          new CustomEvent("cc-backend-action", { detail: { id, action } }),
        );
      },
    });
    _backendPickerRef = picker;
    picker.root.style.display = "none";
    const ctrlBody = panel.querySelector("#comfyclaw-gen-body");
    if (ctrlBody) ctrlBody.appendChild(picker.root);
    const askBackends = () => {
      const ws = _activeSyncClient?.ws;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "list_agent_backends" }));
      }
    };
    setTimeout(askBackends, 800);
    setInterval(askBackends, 30000);
  }

  // 5) Sink for scoreboard cards: appends to the agent log.
  _scoreboardSink = (msg) => {
    const logEl = panel.querySelector("#comfyclaw-think-log");
    if (!logEl) return;
    // Remove the empty-state placeholder if present
    const empty = logEl.querySelector("#comfyclaw-log-empty");
    if (empty) empty.remove();
    const card = buildScoreboardCard(msg, () => {
      const ws = _activeSyncClient?.ws;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "accept_now" }));
        showToast("Accepting current result…", "success", 2500);
      }
    });
    logEl.appendChild(card);
    logEl.scrollTop = logEl.scrollHeight;
    if (_historyTabRef) {
      _historyTabRef.addIterationScore({
        iteration: msg.iteration,
        score: msg.score,
        delta: msg.delta,
        critique: msg.critique,
      });
    }
  };
}

async function exportCurrentWorkflow() {
  try {
    if (typeof app.graphToPrompt === "function") {
      const result = await app.graphToPrompt();
      return result?.output || result?.workflow || null;
    }
  } catch (err) {
    console.warn("[ComfyClaw] Failed to export workflow:", err);
  }
  return Object.keys(_currentApiWorkflow).length > 0
    ? JSON.parse(JSON.stringify(_currentApiWorkflow))
    : null;
}

function setGenRunning(running) {
  _clawPanelRunning = running;
  _isGenerating = running;
  if (!_clawPanel) return;
  const genBtn  = _clawPanel.querySelector("#comfyclaw-gen-btn");
  const stopBtn = _clawPanel.querySelector("#comfyclaw-gen-stop");
  const debugBtn = _clawPanel.querySelector("#comfyclaw-debug-btn");
  const progEl   = _clawPanel.querySelector("#cc-gen-progress");
  const compRun  = _clawPanel.querySelector("#cc-composer-run");
  const compStop = _clawPanel.querySelector("#cc-composer-stop");
  const compAud  = _clawPanel.querySelector("#cc-composer-audit");
  const compProg = _clawPanel.querySelector("#cc-composer-progress");
  genBtn.style.display  = running ? "none" : "";
  stopBtn.style.display = running ? "" : "none";
  if (compRun)  compRun.style.display  = running ? "none" : "";
  if (compStop) compStop.style.display = running ? "" : "none";
  if (compAud)  compAud.disabled = running;
  if (debugBtn) debugBtn.disabled = running;
  if (progEl) progEl.style.display = running ? "block" : "none";
  if (compProg) compProg.style.display = running ? "block" : "none";

  // Tab-strip running indicator
  if (_tabStripRef) {
    _tabStripRef.setBadge("generate", running ? { dot: true, title: "Generation in progress" } : null);
  }

  // Elapsed timer — drive both the action-bar timer and the composer mirror.
  clearInterval(_genTimerInterval);
  const timerEls = [
    document.getElementById("cc-gen-timer"),
    document.getElementById("cc-composer-timer"),
  ].filter(Boolean);
  if (running) {
    _genStartTime = Date.now();
    for (const el of timerEls) { el.style.display = ""; el.textContent = "0:00"; }
    if (timerEls.length) {
      _genTimerInterval = setInterval(() => {
        const s = Math.floor((Date.now() - _genStartTime) / 1000);
        const formatted = `${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;
        for (const el of timerEls) el.textContent = formatted;
      }, 1000);
    }
  } else {
    for (const el of timerEls) el.style.display = "none";
    if (_historyTabRef) {
      _historyTabRef.endRun({ state: _lastGenState || "done" });
    }
  }

  const chatInput = document.getElementById("comfyclaw-think-input");
  if (chatInput) chatInput.placeholder = running
    ? "Send the agent a hint or correction…"
    : "Chat with ComfyClaw about your workflow…";
}

function setGenStatus(state, text) {
  if (!_clawPanel) return;
  const wrap = _clawPanel.querySelector("#comfyclaw-gen-status");
  const textEl = _clawPanel.querySelector("#comfyclaw-gen-status-text");
  // Composer mirrors (single-line variant near the input).
  const compWrap = _clawPanel.querySelector("#cc-composer-status");
  const compText = _clawPanel.querySelector("#cc-composer-status-text");
  if (!wrap) return;
  if (text) {
    wrap.style.display = "flex";
    wrap.dataset.state = state || "idle";
    if (textEl) textEl.textContent = text;
    else wrap.firstChild.textContent = text;
    if (compWrap) {
      compWrap.style.display = "flex";
      compWrap.dataset.state = state || "idle";
    }
    if (compText) compText.textContent = text;
  } else {
    wrap.style.display = "none";
    if (compWrap) compWrap.style.display = "none";
  }
  // Track terminal state so setGenRunning can mark history correctly.
  if (state === "complete")     _lastGenState = "done";
  else if (state === "dry_run_done") _lastGenState = "dry_run";
  else if (state === "error")        _lastGenState = "error";
  if (state === "complete") showToast(text, "success", 3000);
  if (state === "error")    showToast(text.slice(0, 80), "error",   4000);
}

function appendAgentLog(event) {
  if (!_thinkingPanel) return;
  const logEl = _thinkingPanel.querySelector("#comfyclaw-think-log");
  if (!logEl) return;

  _thinkingEntries.push(event);
  if (_thinkingEntries.length > MAX_LOG_ENTRIES) {
    _thinkingEntries.shift();
    if (logEl.firstChild) logEl.removeChild(logEl.firstChild);
  }

  const style = EVENT_STYLES[event.event_type] || EVENT_STYLES.info;
  const entry = document.createElement("div");
  Object.assign(entry.style, {
    marginBottom:  "4px",
    padding:       "5px 9px",
    borderRadius:  "var(--cc-radius-xs)",
    background:    "var(--cc-surface-2)",
    borderLeft:    `3px solid ${style.color}`,
    fontSize:      "12px",
    lineHeight:    "1.4",
    wordBreak:     "break-word",
  });

  const time = event.timestamp
    ? new Date(event.timestamp * 1000).toLocaleTimeString()
    : "";
  const iterBadge = event.iteration
    ? `<span style="color:var(--cc-fg-dim); margin-left:4px;">[iter ${event.iteration}]</span>`
    : "";

  // Remove empty-state placeholder on first real entry
  const emptyEl = logEl.querySelector("#comfyclaw-log-empty");
  if (emptyEl) emptyEl.remove();

  // Entries that benefit from Markdown rendering
  const MD_TYPES = new Set(["strategy", "thinking", "assistant_stream", "assistant_done", "info"]);
  let body;
  if (event.event_type === "tool_call" && event.tool_name) {
    const argsStr = event.tool_args
      ? Object.entries(event.tool_args).map(([k, v]) =>
          `<span style="color:var(--cc-fg-muted);">${escapeHtml(k)}</span>=`
          + `<span style="color:var(--cc-accent-yellow);">${escapeHtml(String(v).slice(0, 80))}</span>`
        ).join(", ")
      : "";
    body = `<span style="color:${style.color}; font-weight:600;">${escapeHtml(event.tool_name)}</span>`
      + (argsStr ? `<br><span style="font-size:11px;">${argsStr}</span>` : "");
  } else if (MD_TYPES.has(event.event_type)) {
    body = renderMarkdown(event.content || "");
  } else {
    body = escapeHtml(event.content || "");
    body = body.replace(/\n/g, "<br>");
  }

  if (event.event_type === "tool_result") {
    entry.style.opacity = "0.75";
    entry.style.fontSize = "11px";
  }

  // assistant_stream entries get a special streaming container
  if (event.event_type === "assistant_stream" && event.message_id) {
    entry.id = `think-stream-${event.message_id}`;
    entry.classList.add("cc-log-entry");
    entry.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px;">
        <span>
          ${style.icon}
          <span style="color:${style.color}; font-weight:600; font-size:11px;">${style.label}</span>
          <span class="cc-spin" style="font-size:10px;margin-left:5px;color:var(--cc-fg-dim);">⟳</span>
        </span>
        <span style="color:var(--cc-fg-dim); font-size:10px;">${time}</span>
      </div>
      <div id="think-stream-body-${event.message_id}" style="white-space:pre-wrap;min-height:16px;">…</div>
    `;
    logEl.appendChild(entry);
    logEl.scrollTop = logEl.scrollHeight;
    return;
  }

  entry.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:2px;">
      <span>${style.icon} <span style="color:${style.color}; font-weight:600; font-size:11px;">${style.label}</span>${iterBadge}</span>
      <div style="display:flex;align-items:center;gap:6px;">
        <button class="cc-msg-copy" title="Copy text"
                style="background:none;border:none;color:var(--cc-fg-dim);cursor:pointer;font-size:11px;
                       padding:1px 4px;border-radius:4px;line-height:1;">⎘</button>
        <span style="color:var(--cc-fg-dim); font-size:10px;">${time}</span>
      </div>
    </div>
    <div class="cc-msg-body">${body}</div>
  `;
  entry.classList.add("cc-log-entry");

  // Wire copy button
  const copyBtn = entry.querySelector(".cc-msg-copy");
  if (copyBtn) {
    copyBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const txt = entry.querySelector(".cc-msg-body")?.innerText || event.content || "";
      navigator.clipboard?.writeText(txt).then(() => showToast("Copied!", "success", 1600));
    });
  }

  logEl.appendChild(entry);

  // Wire code-block copy buttons inside this entry
  entry.querySelectorAll(".cc-copy-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      try {
        const code = decodeURIComponent(escape(atob(btn.dataset.b64 || "")));
        navigator.clipboard?.writeText(code).then(() => {
          btn.textContent = "Copied!";
          setTimeout(() => { btn.textContent = "Copy"; }, 1500);
        });
      } catch(_) { showToast("Copy failed", "error"); }
    });
  });

  // Smart auto-scroll: only scroll if user was near the bottom already
  const wasAtBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 60;
  if (wasAtBottom) logEl.scrollTop = logEl.scrollHeight;

  // Update count badge
  const countEl = document.getElementById("comfyclaw-think-count");
  if (countEl) {
    countEl.textContent = _thinkingEntries.length;
    countEl.style.display = _thinkingEntries.length ? "" : "none";
  }
}

function clearAgentLog() {
  _thinkingEntries = [];
  const logEl = document.getElementById("comfyclaw-think-log");
  if (logEl) {
    logEl.innerHTML = `
      <div id="comfyclaw-log-empty" class="cc-empty"
           style="user-select:none;">
        <div class="cc-empty-icon">💬</div>
        <div class="cc-empty-title">Ready when you are.</div>
        <div>Ask about your workflow, or click
          <strong style="color:var(--cc-accent-green);">▶ Generate</strong>
          to start building.</div>
      </div>
    `;
  }
  const countEl = document.getElementById("comfyclaw-think-count");
  if (countEl) { countEl.textContent = ""; countEl.style.display = "none"; }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket client with auto-reconnect
// ─────────────────────────────────────────────────────────────────────────────

// ── Stable per-tab connection identity ────────────────────────────────────────
// sessionStorage survives page refreshes but is isolated per browser tab,
// so each ComfyUI tab gets its own unique ID.  This maps 1:1 to a _ConnState
// on the Python backend and lets the server route workflow updates, checkpoints,
// and generation results to the right tab.
const _CONNECTION_ID = (() => {
  let id = sessionStorage.getItem("comfyclaw_connection_id");
  if (!id) {
    id = `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`;
    sessionStorage.setItem("comfyclaw_connection_id", id);
  }
  return id;
})();

class SyncClient {
  constructor() {
    this.ws = null;
    this.reconnectAttempts = 0;
    this.destroyed = false;
    this._processing = false;
    this._queue = [];
  }

  connect() {
    const url = localStorage.getItem("comfyclaw_ws_url") || DEFAULT_WS_URL;
    setStatus("connecting");
    _updateConnDot("connecting");
    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.warn("[ComfyClaw] WebSocket construction failed:", err);
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log(`[ComfyClaw] Connected to ${url} (connection_id: ${_CONNECTION_ID})`);
      this.reconnectAttempts = 0;
      clearInterval(this._countdownInterval);
      setStatus("connected");
      _updateConnDot("connected");
      showToast("ComfyClaw connected", "success", 2000);
      // Send hello so the backend can register this tab's _ConnState
      this.ws.send(JSON.stringify({ type: "hello", connection_id: _CONNECTION_ID }));
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this._queue.push(msg);
        this._processQueue();
      } catch (err) {
        console.error("[ComfyClaw] Message parse error:", err);
      }
    };

    this.ws.onerror = () => {};

    this.ws.onclose = () => {
      if (!this.destroyed) {
        setStatus("disconnected");
        _updateConnDot("disconnected");
        this._scheduleReconnect();
      }
    };
  }

  async _processQueue() {
    if (this._processing) return;
    this._processing = true;
    try {
      while (this._queue.length > 0) {
        const msg = this._queue.shift();
        await this._handleMessage(msg);
        if (this._queue.length > 0 && msg.type === "workflow_diff") {
          await sleep(getOpDelay());
        }
      }
    } finally {
      this._processing = false;
    }
  }

  async _handleMessage(msg) {
    if (msg.type === "workflow_update") {
      const wf = msg.workflow || {};
      _currentApiWorkflow = JSON.parse(JSON.stringify(wf));
      const nodeCount = Object.keys(wf).length;
      _updateNodeCount(nodeCount);
      if (nodeCount === 0) {
        console.log("[ComfyClaw] State reset (from-scratch); waiting for new nodes…");
      } else {
        const ok = await loadWorkflowIntoCanvas(wf);
        if (ok) {
          setStatus("updated", `${nodeCount} nodes`);
          // Bind the active session to this workflow if it has no identity yet
          const sess = _activeSession();
          if (sess && !sess.workflowId) {
            const { name } = _detectWorkflowIdentity(wf);
            sess.workflowId = name;
            _persistSessions();
          }
          _renderSessionTabs();
        }
      }
    } else if (msg.type === "workflow_diff" && Array.isArray(msg.ops)) {
      const addCount = msg.ops.filter(o => o.op === "add_node").length;
      const rmCount  = msg.ops.filter(o => o.op === "remove_node").length;
      const updCount = msg.ops.filter(o => o.op === "update_node").length;
      await applyDiffOps(msg.ops);
      const total = Object.keys(_currentApiWorkflow).length;
      _updateNodeCount(total);
      const parts = [];
      if (addCount)  parts.push(`+${addCount}`);
      if (rmCount)   parts.push(`-${rmCount}`);
      if (updCount)  parts.push(`~${updCount}`);
      setStatus("updated", `${total} nodes (${parts.join(", ")})`);
    } else if (msg.type === "request_feedback") {
      console.log("[ComfyClaw] Feedback requested for iteration", msg.iteration);
      showFeedbackPanel(msg);
    } else if (msg.type === "generation_status") {
      const detail = msg.detail || msg.state;
      const iter = msg.iteration ? ` (iter ${msg.iteration})` : "";
      setGenStatus(msg.state, `${detail}${iter}`);
      console.log(`[ComfyClaw] Generation status: ${msg.state}${iter}`);
    } else if (msg.type === "generation_complete") {
      setGenRunning(false);
      const elapsed = _genStartTime ? Math.round((Date.now() - _genStartTime) / 1000) : 0;
      const elapsedStr = elapsed > 0 ? ` · ${elapsed}s` : "";
      // Distinguish a dry-run (no image returned) from a real completion.
      const isDryRun = _lastGenState === "dry_run"
        || (!msg.image && (!msg.images || msg.images.length === 0));
      if (isDryRun) {
        setGenStatus("dry_run_done",
          `🐞 Workflow built (debug) · ${msg.iterations_used || 1} iter${elapsedStr}`);
        appendAgentLog({
          event_type: "info",
          content: `🐞 **Debug run complete** — workflow built without image generation. ${msg.iterations_used || 1} iter${elapsedStr}`,
          timestamp: Date.now() / 1000,
        });
      } else {
        setGenStatus("complete",
          `✓ Done — score ${(msg.score ?? 0).toFixed(2)}, ${msg.iterations_used} iter${elapsedStr}`);
        appendAgentLog({ event_type: "info",
          content: `✅ Generation complete! Score: **${(msg.score ?? 0).toFixed(2)}**, iterations: ${msg.iterations_used}${elapsedStr}`,
          timestamp: Date.now() / 1000 });
      }
      // Phase 4: feed images into the History tab.
      if (_historyTabRef && Array.isArray(msg.images)) {
        for (const img of msg.images) {
          _historyTabRef.addImage({
            filename:  img.filename || img,
            subfolder: img.subfolder || "",
            type:      img.type || "output",
            iteration: msg.iterations_used,
          });
        }
      } else if (_historyTabRef && msg.image) {
        _historyTabRef.addImage({
          filename: msg.image.filename || "",
          subfolder: msg.image.subfolder || "",
          type: msg.image.type || "output",
          iteration: msg.iterations_used,
        });
      }
      console.log("[ComfyClaw] Generation complete:", msg);
    } else if (msg.type === "iteration_score") {
      // Phase 4: live scoreboard card in the agent log + History timeline.
      if (typeof _scoreboardSink === "function") _scoreboardSink(msg);
    } else if (msg.type === "skills_manifest"
            || msg.type === "skill_body"
            || msg.type === "skill_import_result"
            || msg.type === "skill_error") {
      _skillsTabRef?.onMessage(msg);
    } else if (msg.type === "agent_backends") {
      const map = {};
      for (const b of (msg.backends || [])) {
        // New protocol: pass the full status object so the picker can show
        // tri-state availability (ok / needs_auth / needs_install / …).
        map[b.name] = {
          state: b.state || (b.available ? "ok" : "unsupported"),
          detail: b.detail || "",
          binary_path: b.binary_path || "",
          auth_method: b.auth_method || "",
          can_install: !!b.can_install,
        };
      }
      _backendPickerRef?.setAvailability(map);
      // Reflect any auto-demotion (e.g., saved CLI backend is missing on this
      // host → picker falls back to litellm) into the composer chip.
      document.getElementById("cc-composer-backend-chip")
              ?.dispatchEvent(new CustomEvent("cc-backend-refresh"));

    // ── Backend setup flows: install + OAuth ─────────────────────────────────
    } else if (msg.type === "backend_install_progress") {
      // Streamed line from the installer subprocess.
      if (_installModal?.isOpen?.() && typeof _installModal._appendLine === "function") {
        _installModal._appendLine(msg.level || "stdout", msg.line || "");
      }
    } else if (msg.type === "backend_install_complete") {
      if (_installModal?.isOpen?.()) {
        _installModal._inFlight = false;
        if (msg.success) {
          _installModal._setStatus("✓ Installed. You can now sign in.", "ok");
          _installModal._appendLine?.("info", "[install] Complete.");
        } else {
          _installModal._setStatus(`✕ Install failed: ${msg.error || msg.detail || "unknown error"}`, "error");
          _installModal._appendLine?.("error", `[install] ${msg.error || msg.detail || "failed"}`);
        }
      }
      // Re-probe so the chip flips color and the popover lifts the "Install" CTA.
      if (_activeSyncClient?.ws?.readyState === WebSocket.OPEN) {
        _activeSyncClient.ws.send(JSON.stringify({ type: "list_agent_backends" }));
      }
    } else if (msg.type === "backend_auth_url") {
      if (_authModal?.isOpen?.() && typeof _authModal._showSignInLink === "function") {
        _authModal._showSignInLink(msg.url || "");
        _authModal._setStatus("Waiting for you to paste the redirect URL…", "info");
      }
    } else if (msg.type === "backend_auth_progress") {
      if (_authModal?.isOpen?.() && typeof _authModal._setStatus === "function") {
        _authModal._setStatus(msg.message || "", msg.level === "error" ? "error" : "info");
      }
    } else if (msg.type === "backend_auth_complete") {
      if (_authModal?.isOpen?.()) {
        _authModal._inFlight = false;
        if (msg.success) {
          _authModal._showSuccess?.(msg.detail || "Signed in");
        } else {
          _authModal._showFailure?.(msg.error || msg.detail || "Sign-in failed.");
        }
      }
      if (_activeSyncClient?.ws?.readyState === WebSocket.OPEN) {
        _activeSyncClient.ws.send(JSON.stringify({ type: "list_agent_backends" }));
      }

    } else if (msg.type === "generation_error") {
      setGenRunning(false);
      setGenStatus("error", `Error: ${msg.error}`);
      appendAgentLog({ event_type: "error", content: msg.error, timestamp: Date.now() / 1000 });
      console.error("[ComfyClaw] Generation error:", msg.error);
    } else if (msg.type === "agent_event") {
      appendAgentLog(msg);

    // ── Chat streaming ────────────────────────────────────────────────────────
    } else if (msg.type === "chat_response") {
      _appendChatToken(msg.message_id, msg.token, msg.done);

    // ── Checkpoint list update ────────────────────────────────────────────────
    } else if (msg.type === "checkpoints_list") {
      _checkpoints = msg.checkpoints || [];
      _renderCheckpoints();

    } else if (msg.type === "checkpoint_saved") {
      showToast(`📸 Snapshot: ${msg.label || "saved"}`, "success", 2000);

    } else if (msg.type === "checkpoint_restored") {
      if (!msg.success) {
        showToast("Checkpoint restore failed", "error");
        console.warn("[ComfyClaw] Checkpoint restore failed for id:", msg.id);
      }

    // ── Debug results ─────────────────────────────────────────────────────────
    } else if (msg.type === "debug_status") {
      setGenStatus(msg.state, msg.detail || "Running debug…");

    } else if (msg.type === "debug_result") {
      setGenRunning(false);
      _showDebugResult(msg);
    }
  }

  _scheduleReconnect() {
    if (this.destroyed) return;
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      console.warn("[ComfyClaw] Max reconnect attempts reached. Giving up.");
      setStatus("disconnected", "max retries");
      _updateConnDot("disconnected");
      return;
    }
    this.reconnectAttempts++;
    let remaining = Math.ceil(RECONNECT_DELAY_MS / 1000);
    _updateConnDot("reconnecting", remaining);
    clearInterval(this._countdownInterval);
    this._countdownInterval = setInterval(() => {
      remaining--;
      if (remaining > 0) _updateConnDot("reconnecting", remaining);
      else clearInterval(this._countdownInterval);
    }, 1000);
    setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
  }

  destroy() {
    this.destroyed = true;
    this.ws?.close();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Chat Panel
// ─────────────────────────────────────────────────────────────────────────────

let _chatPanel = null;

function createChatPanel() {
  const panel = document.createElement("div");
  panel.id = "comfyclaw-chat-panel";
  panel.style.cssText = `
    position:fixed; bottom:20px; right:420px; width:360px; max-height:520px;
    background:#1e1e2e; border:1px solid #45475a; border-radius:14px;
    box-shadow:0 8px 32px rgba(0,0,0,0.5); z-index:9998;
    display:none; flex-direction:column; font-family:system-ui,sans-serif;
    overflow:hidden;
  `;
  panel.innerHTML = `
    <div style="display:flex; align-items:center; justify-content:space-between;
                padding:10px 14px; border-bottom:1px solid #313244; flex-shrink:0;">
      <span style="font-size:14px; font-weight:700; color:#cdd6f4;">
        💬 ComfyClaw Chat
      </span>
      <button id="comfyclaw-chat-close"
              style="background:none; border:none; color:#a6adc8; cursor:pointer;
                     font-size:18px; line-height:1;">×</button>
    </div>
    <div id="comfyclaw-chat-messages"
         style="flex:1; overflow-y:auto; padding:12px; display:flex;
                flex-direction:column; gap:8px; min-height:0; max-height:380px;">
      <div style="text-align:center; color:#585b70; font-size:12px; padding:20px 0;">
        Ask me anything about your workflow…
      </div>
    </div>
    <div style="padding:10px 12px; border-top:1px solid #313244; flex-shrink:0;">
      <div style="display:flex; gap:6px;">
        <textarea id="comfyclaw-chat-input"
                  placeholder="Type a message…"
                  rows="2"
                  style="flex:1; padding:8px 10px; background:#313244; border:1px solid #45475a;
                         border-radius:8px; color:#cdd6f4; font-size:13px; resize:none;
                         font-family:inherit; outline:none;"></textarea>
        <button id="comfyclaw-chat-send"
                style="padding:8px 12px; border:none; border-radius:8px;
                       background:#89b4fa; color:#1e1e2e; cursor:pointer;
                       font-size:18px; align-self:flex-end;">➤</button>
      </div>
    </div>
  `;

  // ── Close ────────────────────────────────────────────────────────────────
  panel.querySelector("#comfyclaw-chat-close").addEventListener("click", () => {
    panel.style.display = "none";
  });

  // ── Send on click ────────────────────────────────────────────────────────
  const sendMsg = () => {
    const input = panel.querySelector("#comfyclaw-chat-input");
    const text  = input.value.trim();
    if (!text || _chatStreaming) return;
    if (_activeSyncClient?.ws?.readyState !== WebSocket.OPEN) return;

    input.value = "";
    _chatHistory.push({ role: "user", content: text });

    _appendChatBubble("user", text);

    const msgId = `chat_${++_chatMsgIdSeq}`;
    _chatStreaming = true;
    panel.querySelector("#comfyclaw-chat-send").disabled = true;

    // Create a placeholder assistant bubble for streaming
    _createStreamBubble(msgId);

    const _fpp = _activeProvPayload();
    _activeSyncClient.ws.send(JSON.stringify({
      type:          "chat_message",
      message_id:    msgId,
      messages:      _chatHistory,
      api_key:       _fpp.api_key  || undefined,
      api_base:      _fpp.api_base || undefined,
      agent_backend: _backendPickerRef?.value()
                       || localStorage.getItem("comfyclaw_agent_backend")
                       || "litellm",
    }));
  };

  panel.querySelector("#comfyclaw-chat-send").addEventListener("click", sendMsg);
  panel.querySelector("#comfyclaw-chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); }
  });

  document.body.appendChild(panel);
  return panel;
}

// ── Chat toggle button (shows as a bubble beside the 🐾 panel) ───────────────
function createChatToggle() {
  const btn = document.createElement("button");
  btn.id = "comfyclaw-chat-toggle";
  btn.title = "Open ComfyClaw Chat";
  btn.style.cssText = `
    position:fixed; bottom:74px; right:20px; width:42px; height:42px;
    border-radius:50%; border:1px solid #45475a; background:#1e1e2e;
    color:#89b4fa; font-size:20px; cursor:pointer; z-index:9999;
    box-shadow:0 4px 12px rgba(0,0,0,0.4); display:flex;
    align-items:center; justify-content:center;
  `;
  btn.textContent = "💬";
  btn.addEventListener("click", () => {
    if (!_chatPanel) return;
    const shown = _chatPanel.style.display === "flex";
    _chatPanel.style.display = shown ? "none" : "flex";
    if (!shown) {
      _chatPanel.querySelector("#comfyclaw-chat-input")?.focus();
    }
  });
  document.body.appendChild(btn);
  return btn;
}

// ── Chat rendering helpers ────────────────────────────────────────────────────

function _appendChatBubble(role, text) {
  const container = document.getElementById("comfyclaw-chat-messages");
  if (!container) return;

  const isUser = role === "user";
  const wrap = document.createElement("div");
  wrap.style.cssText = `display:flex; justify-content:${isUser ? "flex-end" : "flex-start"};`;

  const bubble = document.createElement("div");
  bubble.style.cssText = `
    max-width:85%; padding:8px 12px; border-radius:12px; font-size:13px;
    line-height:1.5; white-space:pre-wrap; word-break:break-word;
    background:${isUser ? "#89b4fa" : "#313244"};
    color:${isUser ? "#1e1e2e" : "#cdd6f4"};
  `;
  bubble.textContent = text;
  wrap.appendChild(bubble);
  container.appendChild(wrap);
  container.scrollTop = container.scrollHeight;
}

function _createStreamBubble(msgId) {
  const container = document.getElementById("comfyclaw-chat-messages");
  if (!container) return;
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex; justify-content:flex-start;";
  const bubble = document.createElement("div");
  bubble.id = `chat-bubble-${msgId}`;
  bubble.style.cssText = `
    max-width:85%; padding:8px 12px; border-radius:12px; font-size:13px;
    line-height:1.5; white-space:pre-wrap; word-break:break-word;
    background:#313244; color:#cdd6f4;
  `;
  bubble.textContent = "…";
  wrap.appendChild(bubble);
  container.appendChild(wrap);
  container.scrollTop = container.scrollHeight;
}

function _appendChatToken(msgId, token, done) {
  // Route to the thinking-panel streaming body (primary path)
  const thinkBody = document.getElementById(`think-stream-body-${msgId}`);
  if (thinkBody) {
    if (thinkBody.textContent === "…") thinkBody.textContent = "";
    thinkBody.textContent += token;
    const logEl = document.getElementById("comfyclaw-think-log");
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
  }

  // Also update a floating chat bubble if one exists (secondary, optional)
  const bubble = document.getElementById(`chat-bubble-${msgId}`);
  if (bubble) {
    if (bubble.textContent === "…") bubble.textContent = "";
    bubble.textContent += token;
    const container = document.getElementById("comfyclaw-chat-messages");
    if (container) container.scrollTop = container.scrollHeight;
  }

  if (done) {
    _chatStreaming = false;
    _thinkingChatMsgId = null;
    // Re-enable send buttons
    const thinkSend = document.getElementById("comfyclaw-think-send");
    if (thinkSend) thinkSend.disabled = false;
    // Remove streaming spinner
    const entry = document.getElementById(`think-stream-${msgId}`);
    entry?.querySelector(".cc-spin")?.remove();
    // Save raw text to history and render Markdown
    const rawText = thinkBody?.textContent || bubble?.textContent || "";
    if (rawText) {
      _chatHistory.push({ role: "assistant", content: rawText });
      // Replace plain text with Markdown-rendered HTML
      if (thinkBody) {
        thinkBody.innerHTML = renderMarkdown(rawText);
        // Wire code-block copy buttons
        thinkBody.querySelectorAll(".cc-copy-btn").forEach(btn => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            try {
              const code = decodeURIComponent(escape(atob(btn.dataset.b64 || "")));
              navigator.clipboard?.writeText(code).then(() => {
                btn.textContent = "Copied!";
                setTimeout(() => { btn.textContent = "Copy"; }, 1500);
              });
            } catch(_) { showToast("Copy failed", "error"); }
          });
        });
      }
      if (bubble) bubble.innerHTML = renderMarkdown(rawText);
      // Persist to session
      const sess = _activeSession();
      if (sess) { sess.chatHistory = [..._chatHistory]; _persistSessions(); }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Checkpoint renderer
// ─────────────────────────────────────────────────────────────────────────────

function _renderCheckpoints() {
  const list = document.getElementById("comfyclaw-cp-list");
  const section = document.getElementById("comfyclaw-cp-section");
  if (!list || !section) return;

  if (_checkpoints.length === 0) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";
  list.innerHTML = "";

  _checkpoints.forEach(cp => {
    const row = document.createElement("div");
    row.style.cssText = `
      display:flex; align-items:center; gap:6px; padding:5px 8px;
      background:#313244; border-radius:7px; font-size:11px; color:#cdd6f4;
      transition:background 0.15s; cursor:default;
    `;
    row.addEventListener("mouseenter", () => row.style.background = "#45475a");
    row.addEventListener("mouseleave", () => row.style.background = "#313244");

    const now = Date.now() / 1000;
    const ageSec = now - cp.timestamp;
    let age;
    if (ageSec < 60)         age = `${Math.round(ageSec)}s ago`;
    else if (ageSec < 3600)  age = `${Math.round(ageSec/60)}m ago`;
    else                     age = new Date(cp.timestamp * 1000).toLocaleTimeString();

    // Detect type from label prefix
    const isBefore = cp.label.startsWith("Before:");
    const isAfter  = cp.label.startsWith("After:");
    const tagColor = isBefore ? "#f9e2af" : isAfter ? "#a6e3a1" : "#89b4fa";
    const tagIcon  = isBefore ? "●" : isAfter ? "●" : "●";

    row.innerHTML = `
      <span style="color:${tagColor};font-size:9px;flex-shrink:0;">${tagIcon}</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
            title="${escHtml(cp.label)}">${escHtml(cp.label.slice(0,38))}${cp.label.length>38?"…":""}</span>
      <span style="color:#45475a;font-size:10px;flex-shrink:0;">${age}</span>
    `;

    const restoreBtn = document.createElement("button");
    restoreBtn.textContent = "↩";
    restoreBtn.title = "Restore this checkpoint";
    restoreBtn.style.cssText = `
      padding:2px 7px; border:1px solid #45475a; border-radius:5px;
      background:#1e1e2e; color:#a6e3a1; cursor:pointer; font-size:12px;
      flex-shrink:0; transition:all 0.15s;
    `;
    restoreBtn.addEventListener("mouseenter", () => {
      restoreBtn.style.borderColor = "#a6e3a1";
      restoreBtn.style.background = "#a6e3a120";
    });
    restoreBtn.addEventListener("mouseleave", () => {
      restoreBtn.style.borderColor = "#45475a";
      restoreBtn.style.background = "#1e1e2e";
    });
    restoreBtn.addEventListener("click", () => {
      if (_activeSyncClient?.ws?.readyState === WebSocket.OPEN) {
        _activeSyncClient.ws.send(JSON.stringify({ type: "restore_checkpoint", id: cp.id }));
        showToast(`Restored: ${cp.label.slice(0,30)}`, "success");
      }
    });

    row.appendChild(restoreBtn);
    list.appendChild(row);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Debug result display
// ─────────────────────────────────────────────────────────────────────────────

function _showDebugResult(msg) {
  const issues   = msg.issues || [];
  const summary  = msg.summary || "No issues found.";
  const hasFixed = !!msg.fixed_workflow;

  // Log a formatted debug report entry
  const issueLines = issues.map(i => `• **[${i.node_id}]** \`${i.class_type}\`: ${i.detail}`).join("\n");
  const fullContent = summary
    + (issueLines ? "\n\n**Issues found:**\n" + issueLines : "")
    + (hasFixed ? "\n\n✅ Fixed workflow applied to canvas automatically." : "");
  appendAgentLog({ event_type: "info", content: `🔍 Debug Report\n\n${fullContent}`, timestamp: Date.now() / 1000 });

  setGenStatus(
    issues.length === 0 ? "complete" : "error",
    issues.length === 0
      ? "No issues found."
      : `${issues.length} issue(s) found${hasFixed ? " — fix applied" : ""}.`
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ComfyUI extension registration
// ─────────────────────────────────────────────────────────────────────────────

// Host element of the ComfyUI native sidebar tab (set when the tab first mounts).
let _sidebarHostEl = null;

function _attachPanelToSidebarHost() {
  if (!_clawPanel || !_sidebarHostEl) return false;
  if (_clawPanel.parentElement !== _sidebarHostEl) _sidebarHostEl.appendChild(_clawPanel);
  _sidebarHostEl.style.height = "100%";
  _sidebarHostEl.style.padding = "0";
  return true;
}
function _detachPanelToBody() {
  if (!_clawPanel) return;
  if (_clawPanel.parentElement !== document.body) document.body.appendChild(_clawPanel);
}

async function _registerComfyUISidebarTab() {
  // Wait up to ~5s for ComfyUI's extension manager to be ready.
  let waited = 0;
  while (!app?.extensionManager?.registerSidebarTab && waited < 5000) {
    await new Promise((r) => setTimeout(r, 100));
    waited += 100;
  }
  if (!app?.extensionManager?.registerSidebarTab) {
    console.warn("[ComfyClaw] extensionManager.registerSidebarTab unavailable — falling back to floating panel.");
    return false;
  }
  try {
    app.extensionManager.registerSidebarTab({
      id: "comfyclaw",
      icon: "cc-icon-comfyclaw",
      title: "ComfyClaw",
      tooltip: "ComfyClaw — chat with the agent and trigger generations",
      type: "custom",
      render: (el) => {
        _sidebarHostEl = el;
        document.body.dataset.ccHasNativeSidebar = "1";
        if (_clawPanel && (localStorage.getItem("comfyclaw_dock_mode") || "comfy-sidebar") === "comfy-sidebar") {
          _attachPanelToSidebarHost();
          _clawPanel.dataset.dock = "comfy-sidebar";
          if (localStorage.getItem("comfyclaw_hidden") !== "1") {
            _clawPanel.style.display = "flex";
          }
        }
      },
    });
    // If dock mode is comfy-sidebar, activate the tab so render() fires and the
    // panel actually becomes visible. We don't fight ComfyUI if it's already on
    // another tab — just nudge once on first registration.
    const dockMode = localStorage.getItem("comfyclaw_dock_mode") || "comfy-sidebar";
    if (dockMode === "comfy-sidebar") {
      try {
        const st = app.extensionManager.sidebarTab;
        if (st && typeof st.activeSidebarTabId !== "undefined") {
          // Activate only if no other tab is currently active (avoids hijacking).
          if (!st.activeSidebarTabId || localStorage.getItem("comfyclaw_first_run") !== "0") {
            st.activeSidebarTabId = "comfyclaw";
            localStorage.setItem("comfyclaw_first_run", "0");
          }
        }
      } catch (_) {}
    }
    return true;
  } catch (err) {
    console.warn("[ComfyClaw] registerSidebarTab failed:", err);
    return false;
  }
}

app.registerExtension({
  name: "ComfyClaw.SyncBridge",

  async setup() {
    console.log(`[ComfyClaw] Extension loaded — ComfyClaw Sync Bridge v8.0 · connection_id=${_CONNECTION_ID} · Ctrl/Cmd+Shift+Space to toggle panel`);
    statusEl = createStatusBadge();
    _clawPanel = _thinkingPanel = createComfyClawPanel();
    _registerComfyUISidebarTab().then((ok) => {
      // If registration failed and the user had `comfy-sidebar` saved,
      // demote to `sidebar` so the panel still has a usable home.
      if (!ok && (localStorage.getItem("comfyclaw_dock_mode") || "comfy-sidebar") === "comfy-sidebar") {
        localStorage.setItem("comfyclaw_dock_mode", "sidebar");
        if (_clawPanel?._reapplyDock) _clawPanel._reapplyDock();
      }
    });
    setTimeout(() => {
      _activeSyncClient = new SyncClient();
      _activeSyncClient.connect();
    }, 500);

    // ── First-time onboarding toast ────────────────────────────────────
    // Show a single welcome message the first time a user ever opens
    // ComfyUI with ComfyClaw installed. The flag is keyed by version so
    // that a future major release can re-onboard if the UX changes.
    const _WELCOME_KEY = "comfyclaw_seen_welcome_v1";
    if (!localStorage.getItem(_WELCOME_KEY)) {
      // Wait until the badge + panel are actually in the DOM so the toast
      // doesn't fire before the user can act on it.
      setTimeout(() => {
        showToast(
          "🐾 Welcome to ComfyClaw. Type a prompt and click ▶ Generate, " +
          "or open the Skills tab to see what the agent already knows.",
          "info",
          8000
        );
        try { localStorage.setItem(_WELCOME_KEY, String(Date.now())); }
        catch (_) { /* private-mode storage may be unavailable; ignore */ }
      }, 1800);
    }
  },
});
