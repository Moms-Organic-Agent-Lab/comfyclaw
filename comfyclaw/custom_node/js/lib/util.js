/** Tiny utility helpers shared across modules. */

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

export function escAttr(s) {
  return escHtml(s).replace(/"/g, "&quot;");
}

export function escapeHtml(s) {
  return escHtml(s);
}

export const DEFAULT_OP_DELAY_MS = 400;
export function getOpDelay() {
  const stored = localStorage.getItem("comfyclaw_op_delay");
  if (stored !== null) {
    const n = parseInt(stored, 10);
    if (!isNaN(n) && n >= 0) return n;
  }
  return DEFAULT_OP_DELAY_MS;
}

export function shortHash(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h) ^ s.charCodeAt(i);
  return (h >>> 0).toString(16).slice(0, 8);
}

export function nowIso() {
  return new Date().toISOString();
}

export function fmtElapsed(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
