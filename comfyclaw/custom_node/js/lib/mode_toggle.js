/**
 * Mode toggle (Manual / Auto / Co-pilot).
 *
 * Rendered as a 3-state segmented control in the Generate tab.  Below it,
 * a small descriptor line shows what the active mode actually does so users
 * don't have to hover each pill to compare.
 */

import { escHtml } from "./util.js";

const MODES = [
  {
    id: "manual",
    label: "Manual",
    icon: "✋",
    short: "1 round, no verifier — fastest path.",
    desc: "Single round, no verifier — fastest.",
  },
  {
    id: "auto",
    label: "Auto",
    icon: "🤖",
    short: "VLM grades each result, agent re-iterates.",
    desc: "VLM grades each result and the agent re-iterates.",
  },
  {
    id: "copilot",
    label: "Co-pilot",
    icon: "👥",
    short: "VLM scores, you accept or override per round.",
    desc: "VLM + human approval per round.",
  },
];

function _readSavedMode() {
  return localStorage.getItem("comfyclaw_run_mode") || "auto";
}

function _saveMode(mode) {
  localStorage.setItem("comfyclaw_run_mode", mode);
}

/**
 * Build the mode-toggle DOM and wire change events.
 *
 * @param {{ onChange?: (mode:string) => void }} opts
 * @returns {{ root: HTMLElement, value: () => string, set: (m:string) => void }}
 */
export function createModeToggle({ onChange } = {}) {
  const root = document.createElement("div");
  let active = _readSavedMode();

  root.style.cssText = `
    display: flex; flex-direction: column; gap: 4px;
    margin-bottom: 8px;
  `;

  const row = document.createElement("div");
  row.style.cssText = `
    display:flex; gap:4px;
    background:var(--cc-bg); padding:3px; border-radius:10px;
    border:1px solid var(--cc-border);
  `;

  for (const m of MODES) {
    const btn = document.createElement("button");
    btn.dataset.mode = m.id;
    btn.title = m.desc;
    btn.style.cssText = `
      flex:1; border:none; background:transparent;
      color:var(--cc-fg-dim); cursor:pointer;
      padding:7px 4px; border-radius:7px;
      font-size:12px; font-weight:700; letter-spacing:0.2px;
      transition: background 0.15s, color 0.15s, box-shadow 0.15s;
      display:flex; align-items:center; justify-content:center; gap:5px;
    `;
    btn.innerHTML =
      `<span style="font-size:13px;line-height:1;">${m.icon}</span>` +
      `<span>${escHtml(m.label)}</span>`;
    btn.addEventListener("click", () => set(m.id));
    row.appendChild(btn);
  }
  root.appendChild(row);

  // Tagline showing the active mode's behavior in one line.
  const tagline = document.createElement("div");
  tagline.style.cssText = `
    font-size:10.5px; color:var(--cc-fg-dim);
    line-height:1.4; padding-left:2px;
    transition: opacity 0.15s;
  `;
  root.appendChild(tagline);

  function paint() {
    for (const btn of row.querySelectorAll("button")) {
      const isActive = btn.dataset.mode === active;
      btn.style.background = isActive ? "var(--cc-surface-2)" : "transparent";
      btn.style.color      = isActive ? "var(--cc-accent)"    : "var(--cc-fg-dim)";
      btn.style.boxShadow  = isActive ? "var(--cc-shadow-sm)" : "none";
    }
    const m = MODES.find((x) => x.id === active);
    if (m) tagline.innerHTML = `<span style="opacity:0.8;">${m.icon}</span> ${escHtml(m.short)}`;
  }

  function set(m) {
    if (!MODES.find((x) => x.id === m)) return;
    if (active === m) return;
    active = m;
    _saveMode(m);
    paint();
    if (typeof onChange === "function") onChange(m);
  }

  paint();
  return { root, value: () => active, set };
}
