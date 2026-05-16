import { escHtml } from "./util.js";

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

export function showToast(msg, type = "info", duration = 2800) {
  const icons = { success: "✓", error: "✕", info: "ℹ", warning: "⚠" };
  const colors = {
    success: { bg: "rgba(166,227,161,0.13)", border: "var(--cc-accent-green)",  text: "var(--cc-accent-green)" },
    error:   { bg: "rgba(243,139,168,0.13)", border: "var(--cc-accent-red)",    text: "var(--cc-accent-red)" },
    info:    { bg: "rgba(137,180,250,0.13)", border: "var(--cc-accent-blue)",   text: "var(--cc-accent-blue)" },
    warning: { bg: "rgba(249,226,175,0.13)", border: "var(--cc-accent-yellow)", text: "var(--cc-accent-yellow)" },
  };
  const c = colors[type] || colors.info;
  const container = _ensureToastContainer();
  const el = document.createElement("div");
  el.style.cssText = `
    background:${c.bg}; border:1px solid ${c.border}; color:${c.text};
    padding:9px 18px; border-radius:10px; font-size:13px; font-weight:600;
    pointer-events:none; font-family:system-ui,sans-serif;
    backdrop-filter:blur(8px); box-shadow:0 4px 20px rgba(0,0,0,0.4);
    display:flex; align-items:center; gap:8px;
    opacity:0; transition:opacity 0.2s, transform 0.2s;
    transform:translateY(8px);
  `;
  el.innerHTML = `<span style="font-size:16px;line-height:1;">${icons[type] || icons.info}</span><span>${escHtml(msg)}</span>`;
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
