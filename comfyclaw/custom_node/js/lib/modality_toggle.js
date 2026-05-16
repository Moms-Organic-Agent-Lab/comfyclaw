/**
 * Modality toggle (Image / Video).
 *
 * Sits above the Manual / Auto / Co-pilot mode toggle in the Generate tab
 * and controls which output type the harness builds — a single PNG or an
 * animated clip (Wan2.2 / Hunyuan-Video / SVD / AnimateDiff).  Persisted in
 * localStorage so the user's last choice survives a refresh.
 */

import { escHtml } from "./util.js";

const MODALITIES = [
  {
    id: "image",
    label: "Image",
    icon: "🖼",
    short: "Single still — PNG.",
    desc: "Build an image workflow.",
  },
  {
    id: "video",
    label: "Video",
    icon: "🎬",
    short: "Animated clip — Wan2.2 / Hunyuan-Video / SVD.",
    desc: "Build a video workflow.  Requires a video backbone on ComfyUI.",
  },
];

function _readSaved() {
  return localStorage.getItem("comfyclaw_modality") || "image";
}

function _save(m) {
  localStorage.setItem("comfyclaw_modality", m);
}

/**
 * Build the modality-toggle DOM and wire change events.
 *
 * @param {{ onChange?: (modality:string) => void }} opts
 * @returns {{ root: HTMLElement, value: () => string, set: (m:string) => void }}
 */
export function createModalityToggle({ onChange } = {}) {
  const root = document.createElement("div");
  let active = _readSaved();

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

  for (const m of MODALITIES) {
    const btn = document.createElement("button");
    btn.dataset.modality = m.id;
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

  const tagline = document.createElement("div");
  tagline.style.cssText = `
    font-size:10.5px; color:var(--cc-fg-dim);
    line-height:1.4; padding-left:2px;
    transition: opacity 0.15s;
  `;
  root.appendChild(tagline);

  function paint() {
    for (const btn of row.querySelectorAll("button")) {
      const isActive = btn.dataset.modality === active;
      btn.style.background = isActive ? "var(--cc-surface-2)" : "transparent";
      btn.style.color      = isActive ? "var(--cc-accent)"    : "var(--cc-fg-dim)";
      btn.style.boxShadow  = isActive ? "var(--cc-shadow-sm)" : "none";
    }
    const m = MODALITIES.find((x) => x.id === active);
    if (m) tagline.innerHTML = `<span style="opacity:0.8;">${m.icon}</span> ${escHtml(m.short)}`;
  }

  function set(m) {
    if (!MODALITIES.find((x) => x.id === m)) return;
    if (active === m) return;
    active = m;
    _save(m);
    paint();
    if (typeof onChange === "function") onChange(m);
  }

  paint();
  return { root, value: () => active, set };
}
