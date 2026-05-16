/**
 * History tab — generation timeline + image preview gallery.
 *
 * Internally this is a thin client side store; it accumulates entries
 * pushed from the panel logic:
 *
 *   • addImage(payload)        when a generation_complete WS message arrives
 *   • addIterationScore(score) for each iteration_score event
 *   • startRun(meta)           at the start of every Generate click
 *   • endRun({state})          when the run finishes (success or error)
 *
 * Items are persisted in localStorage under `comfyclaw_history_v1` so a
 * page refresh keeps the gallery.
 *
 * The panel exposes a small "↺ Reuse" button per run that fires the
 * `onReusePrompt(prompt)` callback so the host can drop the prompt back
 * into the Generate tab textarea.
 */

import { escHtml } from "./util.js";

const STORE_KEY = "comfyclaw_history_v1";
const MAX_ENTRIES = 80;

function _load() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || []; }
  catch (_) { return []; }
}
function _save(arr) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(arr.slice(-MAX_ENTRIES))); }
  catch (_) {}
}

function _scoreColor(s) {
  if (s == null) return "var(--cc-fg-dim)";
  if (s >= 0.85) return "var(--cc-accent-green)";
  if (s >= 0.65) return "var(--cc-accent-yellow)";
  if (s >= 0.40) return "var(--cc-accent-orange)";
  return "var(--cc-accent-red)";
}

function _comfyImageUrl(img) {
  // ComfyUI exposes images via /view?filename=...&subfolder=...&type=output
  const params = new URLSearchParams({
    filename: img.filename || "",
    subfolder: img.subfolder || "",
    type: img.type || "output",
  });
  return `/view?${params.toString()}`;
}

function _fmtElapsed(ms) {
  if (!ms || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}m ${r}s`;
}

function _fmtRel(ts) {
  if (!ts) return "";
  const diff = Date.now() - ts;
  if (diff < 60_000)        return "just now";
  if (diff < 3_600_000)     return `${Math.floor(diff / 60_000)} min ago`;
  if (diff < 86_400_000)    return `${Math.floor(diff / 3_600_000)} h ago`;
  return new Date(ts).toLocaleDateString();
}

/**
 * Build the History tab.
 *
 * @param {{ onReusePrompt?: (prompt: string) => void }} opts
 */
export function createHistoryTab({ onReusePrompt } = {}) {
  const root = document.createElement("div");
  root.style.cssText = `
    padding: 10px 12px; flex: 1; min-height: 0;
    display: flex; flex-direction: column; gap: 8px; overflow: hidden;
  `;

  root.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="cc-label" style="margin:0;">Generation history</span>
        <span class="cc-pill cc-hist-count" style="display:none;"></span>
      </div>
      <button class="cc-btn cc-btn-secondary cc-hist-clear"
              style="padding:6px 12px;font-size:12px;" title="Clear history">
        <span style="font-size:14px;">🗑</span> Clear
      </button>
    </div>
    <div class="cc-hist-list cc-scroll" style="
      flex:1; min-height:0; overflow-y:auto;
      display:flex; flex-direction:column; gap:8px;
      padding-right:4px;
    "></div>
  `;

  const $list  = root.querySelector(".cc-hist-list");
  const $clear = root.querySelector(".cc-hist-clear");
  const $count = root.querySelector(".cc-hist-count");

  let _entries = _load();

  function _renderEmpty() {
    $list.innerHTML = `
      <div class="cc-empty">
        <div class="cc-empty-icon">🖼</div>
        <div class="cc-empty-title">No runs yet</div>
        <div>Finished generations and dry-runs will land here, with
             iteration scores, critiques, and image previews.</div>
      </div>`;
  }

  function _renderRun(run) {
    const card = document.createElement("div");
    card.className = "cc-card cc-entry-in";
    card.style.cssText = "padding:10px 12px;display:flex;flex-direction:column;gap:8px;";

    const ts      = _fmtRel(run.startedAt);
    const elapsed = run.endedAt && run.startedAt
      ? _fmtElapsed(run.endedAt - run.startedAt) : null;
    const stateColor = ({
      done:       "var(--cc-accent-green)",
      dry_run:    "var(--cc-accent-yellow)",
      error:      "var(--cc-accent-red)",
      running:    "var(--cc-accent-blue)",
    })[run.state || "running"] || "var(--cc-fg-dim)";

    card.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
        <span style="width:6px;height:6px;border-radius:50%;background:${stateColor};
                     ${run.state === "running" ? "animation:cc-pulse 1.4s ease-in-out infinite;" : ""}
                     flex-shrink:0;"></span>
        <span style="font-size:11px;color:var(--cc-fg-muted);">${escHtml(ts)}</span>
        ${run.mode ? `<span class="cc-pill cc-pill-tag"
                           style="color:var(--cc-fg-muted);background:transparent;
                                  border-color:var(--cc-border);">
          ${escHtml(run.mode)}</span>` : ""}
        ${elapsed ? `<span style="font-size:10.5px;color:var(--cc-fg-dim);
                                   font-variant-numeric:tabular-nums;">⏱ ${escHtml(elapsed)}</span>` : ""}
        <span style="flex:1;"></span>
        <button class="cc-icon-btn cc-icon-btn-sm cc-hist-reuse"
                title="Reuse this prompt in the Generate tab">↺</button>
      </div>
      <div style="font-size:12px;line-height:1.4;color:var(--cc-fg);
                  white-space:pre-wrap;word-break:break-word;">${escHtml(run.prompt || "")}</div>
    `;

    // Reuse button — host wires this up via opts.onReusePrompt
    card.querySelector(".cc-hist-reuse")?.addEventListener("click", () => {
      if (typeof onReusePrompt === "function") onReusePrompt(run.prompt || "");
    });

    // Iteration timeline
    if (run.iterations?.length) {
      const tl = document.createElement("div");
      tl.style.cssText = "display:flex;flex-direction:column;gap:3px;font-size:11px;";
      for (const it of run.iterations) {
        const row = document.createElement("div");
        row.style.cssText = `
          display:flex;align-items:center;gap:8px;
          padding:5px 8px; background:var(--cc-bg); border-radius:6px;
          border-left:3px solid ${_scoreColor(it.score)};
        `;
        const pct = it.score == null ? "—" : `${Math.round(it.score * 100)}%`;
        row.innerHTML = `
          <span style="color:var(--cc-fg-dim);min-width:34px;font-variant-numeric:tabular-nums;">
            #${escHtml(it.iteration)}
          </span>
          <span style="font-weight:700;color:${_scoreColor(it.score)};
                       font-variant-numeric:tabular-nums;min-width:38px;">
            ${pct}
          </span>
          ${it.delta != null
            ? `<span style="color:${it.delta >= 0 ? "var(--cc-accent-green)" : "var(--cc-accent-red)"};
                            font-size:12px;line-height:1;">${it.delta >= 0 ? "▲" : "▼"} ${Math.abs(it.delta).toFixed(2)}</span>`
            : ""}
          <span style="color:var(--cc-fg-muted);flex:1;overflow:hidden;
                       text-overflow:ellipsis;white-space:nowrap;font-size:11px;">
            ${escHtml(it.critique || "")}
          </span>
        `;
        tl.appendChild(row);
      }
      card.appendChild(tl);
    }

    // Image gallery
    if (run.images?.length) {
      const gal = document.createElement("div");
      gal.style.cssText = `
        display:grid; grid-template-columns:repeat(auto-fill, minmax(110px, 1fr));
        gap:6px; margin-top:2px;
      `;
      for (const img of run.images) {
        const url = _comfyImageUrl(img);
        const a = document.createElement("a");
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = `${img.filename}${img.iteration ? ` (iter ${img.iteration})` : ""}`;
        a.style.cssText = `
          display:block; aspect-ratio:1/1; overflow:hidden; border-radius:7px;
          border:1px solid var(--cc-border); background:var(--cc-bg);
          transition: transform 0.15s, border-color 0.15s;
        `;
        a.onmouseenter = () => { a.style.transform = "scale(1.03)"; a.style.borderColor = "var(--cc-accent)"; };
        a.onmouseleave = () => { a.style.transform = ""; a.style.borderColor = "var(--cc-border)"; };
        a.innerHTML = `<img src="${url}" alt=""
          loading="lazy"
          style="width:100%;height:100%;object-fit:cover;display:block;">`;
        gal.appendChild(a);
      }
      card.appendChild(gal);
    }

    return card;
  }

  function _render() {
    if (!_entries.length) {
      $count.style.display = "none";
      _renderEmpty();
      return;
    }
    $count.style.display = "";
    $count.textContent = `${_entries.length} run${_entries.length === 1 ? "" : "s"}`;
    $list.innerHTML = "";
    // Newest first
    for (let i = _entries.length - 1; i >= 0; i--) {
      $list.appendChild(_renderRun(_entries[i]));
    }
  }

  function _currentRun() {
    return _entries[_entries.length - 1] || null;
  }

  $clear.addEventListener("click", () => {
    if (!confirm("Clear all generation history?")) return;
    _entries = [];
    _save(_entries);
    _render();
  });

  _render();

  return {
    root,
    /** Begin tracking a new run. */
    startRun({ prompt, mode }) {
      _entries.push({
        startedAt: Date.now(),
        endedAt: null,
        state: "running",
        prompt: prompt || "",
        mode: mode || "",
        iterations: [],
        images: [],
      });
      _save(_entries);
      _render();
    },
    /** Mark the most recent run as finished (success / dry_run / error). */
    endRun({ state } = {}) {
      const run = _currentRun();
      if (!run || run.state !== "running") return;
      run.endedAt = Date.now();
      run.state = state || "done";
      _save(_entries);
      _render();
    },
    /** Append an iteration_score event to the current run. */
    addIterationScore({ iteration, score, delta, critique }) {
      const run = _currentRun();
      if (!run) return;
      run.iterations.push({ iteration, score, delta, critique });
      _save(_entries);
      _render();
    },
    /** Append an image (from generation_complete) to the current run. */
    addImage(img) {
      let run = _currentRun();
      if (!run) {
        run = {
          startedAt: Date.now(), endedAt: null, state: "done",
          prompt: "", mode: "", iterations: [], images: [],
        };
        _entries.push(run);
      }
      run.images.push(img);
      _save(_entries);
      _render();
    },
  };
}
