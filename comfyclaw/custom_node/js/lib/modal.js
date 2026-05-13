/**
 * Minimal modal helper, factored from the in-house ``showBodyModal`` pattern
 * used by the Skills panel.  Returns a handle so the caller can mutate the
 * body dynamically (e.g. stream lines into a log, swap step content) and
 * close the modal programmatically.
 *
 * Usage::
 *
 *   const m = openModal({
 *     title: "Install Claude Code",
 *     body: "<p>Running installer…</p>",
 *     onClose: () => console.log("closed"),
 *   });
 *   m.setBody("<p>Done.</p>");
 *   m.close();
 *
 * Styling uses the existing ``.cc-modal-overlay`` / ``.cc-modal-card`` /
 * ``.cc-modal-header`` / ``.cc-modal-body`` rules from ``lib/styles.js``.
 *
 * Esc, the ✕ button, and clicking outside the card all dismiss the modal
 * by default.  Pass ``dismissable: false`` to lock the modal until the
 * caller closes it programmatically.
 */

import { escHtml } from "./util.js";

export function openModal({
  title = "",
  subtitle = "",
  body = "",
  width = 560,
  dismissable = true,
  onClose,
} = {}) {
  const overlay = document.createElement("div");
  overlay.className = "cc-modal-overlay";

  const card = document.createElement("div");
  card.className = "cc-modal-card";
  card.style.width = `min(${Number(width) || 560}px, 92vw)`;

  card.innerHTML = `
    <div class="cc-modal-header">
      <div style="min-width:0;flex:1;">
        <div class="cc-modal-title"
             style="font-weight:700;color:var(--cc-accent);font-size:14px;">
          ${escHtml(title || "")}
        </div>
        ${
          subtitle
            ? `<div class="cc-modal-subtitle"
                    style="font-size:11px;color:var(--cc-fg-muted);margin-top:2px;">
                 ${escHtml(subtitle)}
               </div>`
            : ""
        }
      </div>
      ${
        dismissable
          ? `<button class="cc-icon-btn cc-modal-close" title="Close (Esc)">✕</button>`
          : ""
      }
    </div>
    <div class="cc-modal-body cc-scroll"></div>
  `;
  overlay.appendChild(card);

  const $body = card.querySelector(".cc-modal-body");
  if (typeof body === "string") {
    $body.innerHTML = body;
  } else if (body instanceof HTMLElement) {
    $body.appendChild(body);
  }

  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (typeof onClose === "function") {
      try { onClose(); } catch { /* swallow */ }
    }
  }

  function onKey(e) {
    if (!dismissable) return;
    if (e.key === "Escape") close();
  }

  if (dismissable) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });
    card.querySelector(".cc-modal-close")?.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
  }

  document.body.appendChild(overlay);

  return {
    overlay,
    card,
    body: $body,
    /** Replace the body's HTML or DOM contents. */
    setBody(next) {
      if (typeof next === "string") $body.innerHTML = next;
      else if (next instanceof HTMLElement) {
        $body.innerHTML = "";
        $body.appendChild(next);
      }
    },
    /** Replace the title text. */
    setTitle(text) {
      card.querySelector(".cc-modal-title").textContent = text || "";
    },
    /** Replace the subtitle text (added if missing). */
    setSubtitle(text) {
      let el = card.querySelector(".cc-modal-subtitle");
      if (!el) {
        el = document.createElement("div");
        el.className = "cc-modal-subtitle";
        el.style.cssText = "font-size:11px;color:var(--cc-fg-muted);margin-top:2px;";
        card.querySelector(".cc-modal-title")?.after(el);
      }
      el.textContent = text || "";
    },
    isOpen: () => !closed,
    close,
  };
}
