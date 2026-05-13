/**
 * Backend picker — chooses the Python AgentBackend driver.
 *
 *   • LiteLLM    — direct API calls (requires API key).
 *   • Claude Code — uses the `claude` CLI (must be installed + signed in).
 *   • Codex      — uses the `codex` CLI.
 *   • Gemini CLI — uses the `gemini` CLI.
 *
 * Availability is fed in from the server via the ``agent_backends`` WS
 * message (see ``SyncServer._dispatch`` -> ``list_agent_backends``).
 *
 * The status map per backend is now an object::
 *
 *   {
 *     state: "ok" | "needs_install" | "needs_auth" | "unsupported" | "error",
 *     binary_path: string,
 *     auth_method: string,
 *     detail: string,
 *     can_install: boolean,
 *   }
 *
 * Booleans are still accepted for backward compatibility.
 *
 * Backends in ``needs_install`` / ``needs_auth`` states are visually
 * distinct (red / amber dot) and clickable: the click fires
 * ``onAction(id, action)`` where ``action`` is ``"install"`` or
 * ``"signin"`` — the caller is expected to open the matching modal.
 */

import { escHtml } from "./util.js";
import { showToast } from "./toast.js";

const BACKENDS = [
  { id: "litellm",     label: "LiteLLM",      desc: "Direct API (Anthropic, OpenAI, Gemini, Groq, Ollama, …)" },
  { id: "claude-code", label: "Claude Code",  desc: "Uses the `claude` CLI." },
  { id: "codex",       label: "Codex",        desc: "Uses the `codex` CLI." },
  { id: "gemini-cli",  label: "Gemini CLI",   desc: "Uses the `gemini` CLI." },
];

function _readSaved() {
  return localStorage.getItem("comfyclaw_agent_backend") || "litellm";
}

function _normalizeStatus(raw) {
  // Boolean form (legacy): true -> ok, false -> unsupported.
  if (raw === true)  return { state: "ok",          detail: "", binary_path: "", auth_method: "", can_install: false };
  if (raw === false) return { state: "unsupported", detail: "", binary_path: "", auth_method: "", can_install: false };
  if (!raw || typeof raw !== "object") return null;
  return {
    state: raw.state || (raw.available ? "ok" : "unsupported"),
    detail: raw.detail || "",
    binary_path: raw.binary_path || "",
    auth_method: raw.auth_method || "",
    can_install: !!raw.can_install,
  };
}

export function createBackendPicker({ onChange, onAction } = {}) {
  const root = document.createElement("div");
  root.style.cssText = `
    display: flex; gap: 5px; margin: 4px 0 8px; flex-wrap: wrap;
  `;
  let active = _readSaved();
  /** @type {Record<string, {state: string, detail: string, binary_path: string, auth_method: string, can_install: boolean}>} */
  let statuses = { litellm: { state: "ok", detail: "", binary_path: "", auth_method: "", can_install: false } };

  function _isUsable(id) {
    const st = statuses[id];
    if (!st) return true;            // unknown = optimistically allow
    return st.state === "ok";
  }

  function _paint() {
    for (const btn of root.querySelectorAll("button[data-backend]")) {
      const id = btn.dataset.backend;
      const st = statuses[id];
      const state = st?.state;
      const isActive = id === active;
      btn.classList.toggle("cc-chip-active", isActive);
      btn.style.opacity = state === "unsupported" ? "0.45" : "1";
      btn.disabled = state === "unsupported";

      const meta = BACKENDS.find((b) => b.id === id);
      btn.title = st?.detail ? `${meta?.label || id}: ${st.detail}` : meta?.desc || "";

      const dot = btn.querySelector(".cc-be-dot");
      if (dot) {
        let color = "var(--cc-fg-faint)";
        if (state === "ok") color = "var(--cc-accent-green)";
        else if (state === "needs_auth") color = "var(--cc-accent, #f0a500)";
        else if (state === "needs_install" || state === "error") color = "var(--cc-accent-red)";
        else if (state === "unsupported") color = "var(--cc-fg-faint)";
        dot.style.background = color;
      }
    }
  }

  function set(id, fire = true) {
    if (!BACKENDS.find((b) => b.id === id)) return;
    if (active === id) return;
    if (!_isUsable(id)) {
      // Explain *why* the chip is rejecting the click instead of silently
      // swallowing it. The previous silent-return was the single most common
      // source of "clicked Claude Code, nothing happens" confusion.
      const st = statuses[id];
      const meta = BACKENDS.find((b) => b.id === id);
      const label = meta?.label || id;
      let msg, kind = "warning";
      switch (st?.state) {
        case "needs_install":
          msg = `${label} CLI not installed. ` +
                (st.detail || "Install it, then click the chip again.");
          break;
        case "needs_auth":
          msg = `${label} is installed but not signed in. ` +
                (st.detail || "Run `claude /login` (or the matching command) in a terminal.");
          break;
        case "unsupported":
          msg = `${label} isn't available in this environment. ` +
                (st.detail || `Make sure the \`${id}\` binary is on $PATH.`);
          break;
        case "error":
          msg = `${label} probe returned an error: ${st.detail || "unknown"}.`;
          kind = "error";
          break;
        default:
          msg = `${label} isn't usable right now.`;
      }
      showToast(msg, kind, 6000);
      return;
    }
    active = id;
    localStorage.setItem("comfyclaw_agent_backend", id);
    _paint();
    if (fire && typeof onChange === "function") onChange(id);
  }

  for (const be of BACKENDS) {
    const btn = document.createElement("button");
    btn.className = "cc-chip";
    btn.dataset.backend = be.id;
    btn.title = be.desc;
    btn.innerHTML = `
      <span class="cc-be-dot" style="width:6px;height:6px;border-radius:50%;
        background:var(--cc-fg-faint);display:inline-block;"></span>
      <span>${escHtml(be.label)}</span>
    `;
    btn.addEventListener("click", () => set(be.id));
    root.appendChild(btn);
  }
  _paint();

  return {
    root,
    value: () => active,
    set,
    /** Return the raw status object for a backend (state/detail/...). */
    status: (id) => statuses[id] || null,
    /** Return the active backend's status object. */
    activeStatus: () => statuses[active] || null,
    /** Update the UI with the latest availability map from the server.
     *
     *  Accepts either the new object-valued map or the legacy boolean map.
     */
    setAvailability(map) {
      for (const [name, raw] of Object.entries(map || {})) {
        const norm = _normalizeStatus(raw);
        if (norm) statuses[name] = norm;
      }
      // If the currently-selected backend is no longer ok, demote to litellm.
      if (!_isUsable(active)) {
        active = "litellm";
        localStorage.setItem("comfyclaw_agent_backend", active);
      }
      _paint();
    },
    /** Programmatically request an action ("install" | "signin") for an id.
     *  Wired to the popover's action buttons. */
    triggerAction(id, action) {
      if (typeof onAction === "function") onAction(id, action);
    },
  };
}
