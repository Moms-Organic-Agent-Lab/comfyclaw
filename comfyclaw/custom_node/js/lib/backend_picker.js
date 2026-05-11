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
 * Unavailable backends render disabled with a tooltip.
 */

import { escHtml } from "./util.js";

const BACKENDS = [
  { id: "litellm",     label: "LiteLLM",      desc: "Direct API (Anthropic, OpenAI, Gemini, Groq, Ollama, …)" },
  { id: "claude-code", label: "Claude Code",  desc: "Uses the `claude` CLI." },
  { id: "codex",       label: "Codex",        desc: "Uses the `codex` CLI." },
  { id: "gemini-cli",  label: "Gemini CLI",   desc: "Uses the `gemini` CLI." },
];

function _readSaved() {
  return localStorage.getItem("comfyclaw_agent_backend") || "litellm";
}

export function createBackendPicker({ onChange } = {}) {
  const root = document.createElement("div");
  root.style.cssText = `
    display: flex; gap: 5px; margin: 4px 0 8px; flex-wrap: wrap;
  `;
  let active = _readSaved();
  let availability = { litellm: true };

  function _paint() {
    for (const btn of root.querySelectorAll("button[data-backend]")) {
      const id = btn.dataset.backend;
      const avail = availability[id];
      const isActive = id === active;
      btn.classList.toggle("cc-chip-active", isActive);
      btn.style.opacity = avail === false ? "0.45" : "1";
      btn.disabled = avail === false;

      const dot = btn.querySelector(".cc-be-dot");
      if (dot) {
        dot.style.background =
          avail === false ? "var(--cc-accent-red)"
          : avail === true ? "var(--cc-accent-green)"
          : "var(--cc-fg-faint)";
      }
    }
  }

  function set(id, fire = true) {
    if (!BACKENDS.find((b) => b.id === id)) return;
    if (active === id) return;
    if (availability[id] === false) return;
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
    /** Update the UI with the latest availability map from the server. */
    setAvailability(map) {
      availability = { ...availability, ...map };
      // If the currently-selected backend is now unavailable, fall back.
      if (availability[active] === false) {
        active = "litellm";
        localStorage.setItem("comfyclaw_agent_backend", active);
      }
      _paint();
    },
  };
}
