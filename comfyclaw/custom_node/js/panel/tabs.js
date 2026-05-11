/**
 * Tab strip + container for the ComfyClaw panel.
 *
 * Builds a small tab bar and content slots.  Each tab can show an
 * optional badge (text count or pulsing dot) which the host updates via
 * the returned ``setBadge`` API.
 *
 * Usage:
 *
 *     const strip = createTabStrip({
 *       tabs: [
 *         { id: "generate", label: "Generate", icon: "✨" },
 *         { id: "skills",   label: "Skills",   icon: "📚",
 *           onActivate: () => skillsTab.refresh() },
 *         { id: "history",  label: "History",  icon: "🖼" },
 *       ],
 *     });
 *     strip.bindSlot("generate", generateSlot);
 *     strip.setBadge("history", { count: 3 });          // pill with "3"
 *     strip.setBadge("generate", { dot: true });        // pulsing dot
 *     strip.setBadge("history",  null);                 // clear
 */

import { escHtml } from "../lib/util.js";

export function createTabStrip({ tabs, initial = "" }) {
  const root = document.createElement("div");
  root.style.cssText = `
    display: flex; align-items: stretch;
    border-bottom: 1px solid var(--cc-border);
    background: var(--cc-surface);
    padding: 0 6px;
    flex-shrink: 0;
  `;

  let activeId = initial || tabs[0]?.id;
  const slots = {};
  // Remember each slot's original `display` value so we can restore it when
  // the tab is reactivated.  Setting `style.display = ""` would fall back to
  // the default `block`, which collapses any nested flex column the slot was
  // built around (this used to break #comfyclaw-think-log scrolling).
  const slotDisplays = {};
  const badges = {};

  function _paint() {
    for (const t of tabs) {
      const btn = root.querySelector(`button[data-tab="${t.id}"]`);
      const slot = slots[t.id];
      const active = t.id === activeId;
      if (btn) btn.classList.toggle("cc-tab-active", active);
      if (slot) {
        slot.style.display = active ? (slotDisplays[t.id] || "flex") : "none";
      }
    }
  }

  function activate(id, fire = true) {
    if (!tabs.find((t) => t.id === id)) return;
    if (activeId === id) return;
    activeId = id;
    _paint();
    if (fire) {
      const t = tabs.find((x) => x.id === id);
      if (typeof t?.onActivate === "function") t.onActivate();
    }
  }

  function _renderBadge(id) {
    const btn = root.querySelector(`button[data-tab="${id}"]`);
    if (!btn) return;
    const slot = btn.querySelector(".cc-tab-badge-slot");
    if (!slot) return;
    const b = badges[id];
    if (!b || (b.count == null && !b.dot)) {
      slot.innerHTML = "";
      return;
    }
    if (b.dot) {
      slot.innerHTML = `<span class="cc-tab-pill"
        title="${b.title ? escHtml(b.title) : "Active"}">
          <span class="cc-tab-pill-dot"></span>
        </span>`;
    } else {
      slot.innerHTML = `<span class="cc-tab-pill"
        title="${b.title ? escHtml(b.title) : ""}">${escHtml(String(b.count))}</span>`;
    }
  }

  for (const t of tabs) {
    const btn = document.createElement("button");
    btn.className = "cc-tab-button";
    btn.dataset.tab = t.id;
    btn.title = t.title || t.label;
    btn.innerHTML =
      `${t.icon ? `<span style="line-height:1;">${t.icon}</span>` : ""}` +
      `<span>${escHtml(t.label)}</span>` +
      `<span class="cc-tab-badge-slot"></span>`;
    btn.addEventListener("click", () => activate(t.id));
    root.appendChild(btn);
  }

  return {
    root,
    /** Register the slot element for one of the tabs. Hidden until activated. */
    bindSlot(id, el) {
      slots[id] = el;
      // Capture the slot's intended display (set by the caller via cssText).
      // Falls back to the default flex column if nothing was set.
      const cur = el.style.display;
      slotDisplays[id] = cur && cur !== "none" ? cur : "flex";
      _paint();
    },
    activate,
    value: () => activeId,
    /**
     * Update a tab's badge.  Pass ``null`` (or an empty object) to clear.
     *   { count: 3 }            → numeric pill
     *   { dot: true }           → pulsing dot
     *   { count, title }        → with hover tooltip
     */
    setBadge(id, badge) {
      badges[id] = badge || null;
      _renderBadge(id);
    },
  };
}
