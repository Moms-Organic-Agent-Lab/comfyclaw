/**
 * Live "iteration scoreboard" widget.
 *
 * Rendered inline in the agent log every time the backend emits an
 * ``iteration_score`` event.  Shows:
 *   • Iteration label + (when known) progress (e.g. "Iter 2 / 3")
 *   • Score (0–1) as a colored bar + percentage,
 *   • Δ vs previous iteration,
 *   • Verifier critique (collapsible),
 *   • An "Accept now" button that sends an ``accept_now`` WS message so
 *     the harness exits its loop early.
 */

import { escHtml } from "./util.js";
import { renderMarkdown } from "./markdown.js";

function _scoreColor(score) {
  if (score == null) return "var(--cc-fg-dim)";
  if (score >= 0.85) return "var(--cc-accent-green)";
  if (score >= 0.65) return "var(--cc-accent-yellow)";
  if (score >= 0.40) return "var(--cc-accent-orange)";
  return "var(--cc-accent-red)";
}

function _deltaBadge(delta) {
  if (delta == null) return "";
  const sign = delta > 0 ? "▲" : delta < 0 ? "▼" : "·";
  const color = delta > 0 ? "var(--cc-accent-green)"
              : delta < 0 ? "var(--cc-accent-red)"
              : "var(--cc-fg-dim)";
  return `<span style="color:${color};font-weight:700;font-size:11px;">${sign} ${Math.abs(delta).toFixed(2)}</span>`;
}

/**
 * Build one scoreboard card DOM node.
 *
 * @param {object} ev  iteration_score payload from the server.
 *                     Shape: { iteration, total?, score, delta?, critique? }
 * @param {() => void} onAccept  callback fired when user clicks "Accept now".
 */
export function buildScoreboardCard(ev, onAccept) {
  const card = document.createElement("div");
  card.className = "cc-card cc-entry-in";
  card.style.cssText = `
    margin: 6px 0 4px;
    border-left: 3px solid ${_scoreColor(ev.score)};
    padding: 10px 12px;
    display: flex; flex-direction: column; gap: 8px;
  `;

  const score = ev.score;
  const pct = score == null ? null : Math.round(score * 100);

  // Build the iteration progress label.  When the server passes `total`
  // we render "Iter 2 / 3"; otherwise just "Iteration 2" or generic.
  let iterLabel = "Iteration";
  if (ev.iteration != null) {
    iterLabel = ev.total
      ? `Iter ${ev.iteration} / ${ev.total}`
      : `Iteration ${ev.iteration}`;
  }

  card.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;
                gap:8px;flex-wrap:wrap;">
      <div style="display:flex;align-items:baseline;gap:10px;flex:1;min-width:0;">
        <span class="cc-pill cc-pill-tag"
              style="color:var(--cc-fg-muted);background:transparent;
                     border-color:var(--cc-border);">
          ${escHtml(iterLabel)}
        </span>
        ${pct != null
          ? `<span style="font-size:20px;font-weight:800;color:${_scoreColor(score)};
                          font-variant-numeric:tabular-nums;line-height:1;">
              ${pct}<span style="font-size:11px;font-weight:600;opacity:0.6;">%</span>
            </span>`
          : `<span style="font-size:11px;color:var(--cc-fg-dim);font-style:italic;">no verifier</span>`}
        ${_deltaBadge(ev.delta)}
      </div>
      <button class="cc-btn cc-btn-primary cc-accept-btn"
              style="padding:5px 12px;font-size:11px;flex-shrink:0;">
        ✓ Accept now
      </button>
    </div>
    ${pct != null ? `
      <div class="cc-score-bar-bg">
        <div class="cc-score-bar-fg" style="width:${pct}%;background:${_scoreColor(score)};"></div>
      </div>` : ""}
    ${ev.critique ? `
      <details style="font-size:11px;line-height:1.45;color:var(--cc-fg-muted);">
        <summary style="cursor:pointer;color:var(--cc-fg-dim);outline:none;
                        list-style:none;display:flex;align-items:center;gap:4px;">
          <span class="cc-critique-arrow">▸</span>
          Verifier critique
        </summary>
        <div class="cc-critique-body" style="margin-top:6px;
                                             padding:8px 10px;background:var(--cc-bg);
                                             border-radius:6px;
                                             border:1px solid var(--cc-border);">
          ${renderMarkdown(ev.critique)}
        </div>
      </details>` : ""}
  `;

  // Animated arrow on details toggle
  card.querySelector("details")?.addEventListener("toggle", (e) => {
    const arrow = card.querySelector(".cc-critique-arrow");
    if (arrow) arrow.textContent = e.target.open ? "▾" : "▸";
  });

  // Wire the "Accept now" button (also disables itself once clicked)
  card.querySelector(".cc-accept-btn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.target.disabled = true;
    e.target.textContent = "Accepted ✓";
    e.target.style.opacity = "0.7";
    if (typeof onAccept === "function") onAccept();
  });

  return card;
}
