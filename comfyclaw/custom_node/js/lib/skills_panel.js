/**
 * Skills tab — full skills browser.
 *
 *   • List rows with toggle, source icon + label, view-body, delete.
 *   • Search box that filters by name + description.
 *   • Import buttons: Folder path, .zip upload, Git URL.
 *   • Clicking a row's view button opens a slide-up modal with the SKILL.md body.
 *
 * Talks to the Python ``SyncServer`` via WS messages:
 *   out: list_skills, set_skill_enabled, read_skill_body,
 *        import_skill_folder, import_skill_zip, import_skill_git, delete_skill
 *   in:  skills_manifest, skill_body, skill_import_result, skill_error
 */

import { escHtml } from "./util.js";
import { renderMarkdown } from "./markdown.js";
import { showToast } from "./toast.js";

const SOURCE_META = {
  builtin: { label: "built-in", icon: "📦", color: "var(--cc-accent-blue)" },
  local:   { label: "folder",   icon: "📁", color: "var(--cc-accent-green)" },
  zip:     { label: "zip",      icon: "🗜",  color: "var(--cc-accent-yellow)" },
  git:     { label: "git",      icon: "🌐", color: "var(--cc-accent-orange)" },
  extra:   { label: "extra",    icon: "✦",  color: "var(--cc-fg-muted)" },
};

function _send(ws, msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showToast("Not connected to backend.", "warning");
    return false;
  }
  ws.send(JSON.stringify(msg));
  return true;
}

/**
 * Build the Skills tab.
 * @param {{ getWs: () => WebSocket | null }} ctx
 */
export function createSkillsTab({ getWs }) {
  const root = document.createElement("div");
  root.style.cssText = `
    padding: 10px 12px; flex: 1; min-height: 0;
    display: flex; flex-direction: column; gap: 8px;
    overflow: hidden;
  `;

  root.innerHTML = `
    <!-- Search + actions row -->
    <div style="display:flex; gap:6px; align-items:center;">
      <input class="cc-input cc-skill-search" type="search"
             placeholder="Search skills…" style="flex:1;font-size:12px;padding:6px 10px;">
      <button class="cc-icon-btn cc-skill-refresh" title="Reload from disk">↻</button>
    </div>

    <!-- Filter chips (source × enabled) -->
    <div class="cc-skill-filters" style="display:flex; gap:4px; flex-wrap:wrap; align-items:center;">
      <button class="cc-chip cc-skill-source-chip cc-chip-active" data-source="all">All</button>
      <button class="cc-chip cc-skill-source-chip" data-source="builtin">📦 Built-in</button>
      <button class="cc-chip cc-skill-source-chip" data-source="user">👤 User</button>
      <span style="width:1px; align-self:stretch; background:var(--cc-border); margin:2px 4px;"></span>
      <button class="cc-chip cc-skill-state-chip cc-chip-active" data-state="all"
              title="Show all skills">Any</button>
      <button class="cc-chip cc-skill-state-chip" data-state="enabled"
              title="Show only enabled skills">On</button>
      <button class="cc-chip cc-skill-state-chip" data-state="disabled"
              title="Show only disabled skills">Off</button>
    </div>

    <!-- Import + bulk action bar -->
    <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;">
      <button class="cc-btn cc-btn-secondary cc-skill-import-folder"
              style="font-size:11px;padding:6px 10px;" title="Import from a local folder">
        <span style="opacity:0.85;">📁</span> Folder
      </button>
      <button class="cc-btn cc-btn-secondary cc-skill-import-zip"
              style="font-size:11px;padding:6px 10px;" title="Upload a .zip">
        <span style="opacity:0.85;">🗜</span> .zip
      </button>
      <button class="cc-btn cc-btn-secondary cc-skill-import-git"
              style="font-size:11px;padding:6px 10px;" title="Clone from a git URL">
        <span style="opacity:0.85;">🌐</span> Git
      </button>
      <span style="flex:1;"></span>
      <button class="cc-icon-btn cc-icon-btn-sm cc-skill-bulk-on"
              title="Enable every skill matching the current filter">✓</button>
      <button class="cc-icon-btn cc-icon-btn-sm cc-skill-bulk-off"
              title="Disable every skill matching the current filter">⊘</button>
      <input type="file" accept=".zip" class="cc-skill-zip-input" style="display:none;">
    </div>

    <!-- Roots / status -->
    <div class="cc-skill-status" style="font-size:10.5px;color:var(--cc-fg-dim);"></div>

    <!-- List -->
    <div class="cc-skill-list cc-scroll" style="
      flex:1; min-height:0; overflow-y:auto;
      display:flex; flex-direction:column; gap:5px;
      padding-right:4px;
    "></div>

    <!-- Modal mount -->
    <div class="cc-skill-modal-mount"></div>
  `;

  const $search    = root.querySelector(".cc-skill-search");
  const $refresh   = root.querySelector(".cc-skill-refresh");
  const $listEl    = root.querySelector(".cc-skill-list");
  const $status    = root.querySelector(".cc-skill-status");
  const $btnFolder = root.querySelector(".cc-skill-import-folder");
  const $btnZip    = root.querySelector(".cc-skill-import-zip");
  const $btnGit    = root.querySelector(".cc-skill-import-git");
  const $zipInput  = root.querySelector(".cc-skill-zip-input");
  const $modalMount = root.querySelector(".cc-skill-modal-mount");

  let _manifest = [];
  let _filter = "";
  let _sourceFilter = "all";   // "all" | "builtin" | "user"
  let _stateFilter  = "all";   // "all" | "enabled" | "disabled"
  let _gotManifest = false;    // server has answered list_skills at least once
  let _lastFetchAt = 0;        // wall-clock of the most recent list_skills send

  function _wsState() {
    const ws = getWs();
    if (!ws) return "no-ws";
    return ({
      [WebSocket.CONNECTING]: "connecting",
      [WebSocket.OPEN]:       "open",
      [WebSocket.CLOSING]:    "closing",
      [WebSocket.CLOSED]:     "closed",
    })[ws.readyState] || "unknown";
  }

  function _matches(skill) {
    // Source chip
    if (_sourceFilter === "builtin" && !skill.builtin) return false;
    if (_sourceFilter === "user"    && skill.builtin)  return false;
    // State chip
    if (_stateFilter === "enabled"  && !skill.enabled) return false;
    if (_stateFilter === "disabled" && skill.enabled)  return false;
    // Text search
    if (!_filter) return true;
    const f = _filter.toLowerCase();
    return (
      skill.name.toLowerCase().includes(f) ||
      (skill.description || "").toLowerCase().includes(f) ||
      (skill.location || "").toLowerCase().includes(f)
    );
  }

  function _sourceMeta(s) {
    return SOURCE_META[s] || { label: s || "—", icon: "•", color: "var(--cc-fg-dim)" };
  }

  function _renderEmpty(total) {
    // Pick an empty state that matches *why* the list is empty so users can
    // self-diagnose. The common confusing case is "WS isn't connected and
    // the panel never received a manifest" — previously rendered identically
    // to "manifest received, no skills imported".
    const wsState = _wsState();
    const wsOpen  = wsState === "open";

    if (total > 0) {
      // We DO have skills but the filter knocked them out.
      $listEl.innerHTML = `
        <div class="cc-empty">
          <div class="cc-empty-icon">🔍</div>
          <div class="cc-empty-title">No matches.</div>
          <div>Try a different search or reset the filter chips.</div>
        </div>`;
      return;
    }

    if (!wsOpen) {
      // Server not reachable. Surface this loudly — it's the most common
      // cause of an empty Skills tab.
      $listEl.innerHTML = `
        <div class="cc-empty">
          <div class="cc-empty-icon" style="opacity:0.5;">🔌</div>
          <div class="cc-empty-title" style="color:var(--cc-accent-red);">
            Not connected to ComfyClaw sync server
          </div>
          <div>
            WebSocket state: <strong>${escHtml(wsState)}</strong>.<br>
            Start the server (<code>comfyclaw run</code>) and the list will
            populate automatically.
          </div>
          <div class="cc-empty-actions">
            <button class="cc-btn cc-btn-secondary cc-empty-retry"
                    style="font-size:11px;padding:6px 10px;">
              ↻ Retry now
            </button>
          </div>
        </div>`;
      $listEl.querySelector(".cc-empty-retry")?.addEventListener("click", refresh);
      return;
    }

    if (!_gotManifest) {
      // WS is open but the manifest hasn't arrived yet.
      $listEl.innerHTML = `
        <div class="cc-empty">
          <div class="cc-empty-icon">⏳</div>
          <div class="cc-empty-title">Fetching skills…</div>
          <div>
            Connected — waiting for the server to list its skill roots.<br>
            If this hangs for more than a few seconds, check the server log
            for <code>[SkillsRegistry] loaded N skills from: …</code>.
          </div>
        </div>`;
      return;
    }

    // WS is open, manifest arrived, but it has zero skills. Either the
    // server's builtin folder really is empty (uncommon) or the install is
    // on an older version that doesn't ship the curated skills.
    $listEl.innerHTML = `
      <div class="cc-empty">
        <div class="cc-empty-icon">📚</div>
        <div class="cc-empty-title">Server returned an empty skill list.</div>
        <div>
          The connection works, but no skills were found in the registry
          roots. Confirm the server printed <code>[SkillsRegistry] loaded …</code>
          at startup; if not, the install may be missing the builtin folder
          (<code>comfyclaw/skills/</code>).
        </div>
        <div class="cc-empty-actions">
          <button class="cc-btn cc-btn-secondary cc-empty-folder" style="font-size:11px;padding:6px 10px;">
            📁 Import folder
          </button>
          <button class="cc-btn cc-btn-secondary cc-empty-zip" style="font-size:11px;padding:6px 10px;">
            🗜 Upload .zip
          </button>
          <button class="cc-btn cc-btn-secondary cc-empty-git" style="font-size:11px;padding:6px 10px;">
            🌐 From git
          </button>
        </div>
      </div>`;
    $listEl.querySelector(".cc-empty-folder")?.addEventListener("click", _onImportFolder);
    $listEl.querySelector(".cc-empty-zip"   )?.addEventListener("click", () => $zipInput.click());
    $listEl.querySelector(".cc-empty-git"   )?.addEventListener("click", _onImportGit);
  }

  function _render() {
    const total   = _manifest.length;
    const enabled = _manifest.filter((s) => s.enabled).length;
    const visible = _manifest.filter(_matches).length;

    // Header: enabled/total + visible-count when a filter is narrowing things.
    let statusText = total ? `${enabled}/${total} skills enabled` : "No skills yet — import one below.";
    if (total && visible !== total) statusText += ` · ${visible} match`;
    $status.textContent = statusText;

    // Reflect chip-active state from the filter values (in case a code path
    // set them programmatically, e.g. on reload).
    root.querySelectorAll(".cc-skill-source-chip").forEach((c) => {
      c.classList.toggle("cc-chip-active", c.dataset.source === _sourceFilter);
    });
    root.querySelectorAll(".cc-skill-state-chip").forEach((c) => {
      c.classList.toggle("cc-chip-active", c.dataset.state === _stateFilter);
    });

    const rows = _manifest.filter(_matches);
    if (!rows.length) {
      _renderEmpty(total);
      return;
    }

    $listEl.innerHTML = "";
    for (const sk of rows) {
      const meta = _sourceMeta(sk.source);
      const row = document.createElement("div");
      row.className = "cc-card";
      // Disabled rows visibly recede so users can scan enabled skills first.
      const baseBorder = sk.enabled ? "var(--cc-border)" : "var(--cc-fg-faint)";
      row.style.cssText = `
        padding: 8px 10px;
        display: flex; align-items: center; gap: 9px;
        border: 1px solid ${baseBorder};
        ${sk.enabled ? "" : "opacity:0.62;"}
        transition: border-color 0.15s, background 0.15s, opacity 0.15s;
      `;
      row.onmouseenter = () => { row.style.borderColor = "var(--cc-fg-dim)"; };
      row.onmouseleave = () => { row.style.borderColor = baseBorder; };

      row.innerHTML = `
        <input type="checkbox" class="cc-sk-toggle" ${sk.enabled ? "checked" : ""}
               style="cursor:pointer;flex-shrink:0;accent-color:var(--cc-accent);
                      width:14px;height:14px;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <span style="font-weight:700;color:var(--cc-fg);font-size:12px;
                         font-family:monospace;">${escHtml(sk.name)}</span>
            <span class="cc-pill cc-pill-tag"
                  title="Source: ${escHtml(meta.label)}"
                  style="color:${meta.color};border-color:${meta.color};
                         background:transparent;">
              <span style="font-size:10px;line-height:1;opacity:0.95;">${meta.icon}</span>
              <span>${escHtml(meta.label)}</span>
            </span>
          </div>
          <div style="font-size:11px;color:var(--cc-fg-muted);
                      margin-top:2px;line-height:1.4;
                      overflow:hidden;text-overflow:ellipsis;
                      display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;">
            ${escHtml(sk.description)}
          </div>
        </div>
        <button class="cc-icon-btn cc-icon-btn-sm cc-sk-view" title="View body">📖</button>
        ${sk.builtin ? "" : `
          <button class="cc-icon-btn cc-icon-btn-sm cc-sk-del"
                  title="Delete skill"
                  style="color:var(--cc-accent-red);">✕</button>`}
      `;

      row.querySelector(".cc-sk-toggle").addEventListener("change", (e) => {
        _send(getWs(), { type: "set_skill_enabled", name: sk.name, enabled: e.target.checked });
      });
      row.querySelector(".cc-sk-view").addEventListener("click", () => {
        _send(getWs(), { type: "read_skill_body", name: sk.name });
      });
      row.querySelector(".cc-sk-del")?.addEventListener("click", () => {
        if (!confirm(`Delete skill "${sk.name}"? This removes its files from disk.`)) return;
        _send(getWs(), { type: "delete_skill", name: sk.name });
      });

      $listEl.appendChild(row);
    }
  }

  // ── Import handlers ──────────────────────────────────────────────────────
  function _onImportFolder() {
    const path = prompt("Path to a folder containing SKILL.md:");
    if (!path) return;
    _send(getWs(), { type: "import_skill_folder", path });
  }
  function _onImportGit() {
    const url = prompt("Git URL of a repo containing SKILL.md (or a top-level skill folder):");
    if (!url) return;
    const ref = prompt("Branch / tag / ref (optional):") || null;
    _send(getWs(), { type: "import_skill_git", url, ref });
  }

  $btnFolder.addEventListener("click", _onImportFolder);
  $btnZip   .addEventListener("click", () => $zipInput.click());
  $btnGit   .addEventListener("click", _onImportGit);

  $zipInput.addEventListener("change", () => {
    const f = $zipInput.files?.[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result || "";
      const idx = dataUrl.indexOf(",");
      const b64 = idx >= 0 ? dataUrl.slice(idx + 1) : dataUrl;
      _send(getWs(), { type: "import_skill_zip", filename: f.name, base64: b64 });
    };
    reader.readAsDataURL(f);
    $zipInput.value = "";
  });

  // ── Search filter ────────────────────────────────────────────────────────
  $search.addEventListener("input", () => {
    _filter = $search.value.trim();
    _render();
  });
  // Escape clears + blurs.
  $search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      if ($search.value) {
        $search.value = "";
        _filter = "";
        _render();
      } else {
        $search.blur();
      }
    }
  });
  // Hint the shortcut in the placeholder so it's discoverable.
  $search.placeholder = "Search skills…   (press /)";
  // Global `/` to focus the search input. We only listen while this tab is in
  // the DOM (createSkillsTab returns the root; the parent panel mounts /
  // unmounts it). Skip if the event already targets an input/textarea so
  // typing `/` inside a prompt box doesn't get hijacked.
  function _onGlobalKey(e) {
    if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!root.isConnected) return;          // tab is not mounted
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    e.preventDefault();
    $search.focus();
    $search.select();
  }
  document.addEventListener("keydown", _onGlobalKey);

  // ── Source + state chip filters ─────────────────────────────────────────
  root.querySelectorAll(".cc-skill-source-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      _sourceFilter = chip.dataset.source || "all";
      _render();
    });
  });
  root.querySelectorAll(".cc-skill-state-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      _stateFilter = chip.dataset.state || "all";
      _render();
    });
  });

  // ── Bulk enable / disable across the current filter ─────────────────────
  function _bulkSet(enabled) {
    const visible = _manifest.filter(_matches);
    const targets = visible.filter((s) => !!s.enabled !== enabled);
    if (!targets.length) {
      showToast(`No visible skills to ${enabled ? "enable" : "disable"}.`, "info", 1800);
      return;
    }
    const verb = enabled ? "Enable" : "Disable";
    if (!confirm(`${verb} ${targets.length} visible skill${targets.length > 1 ? "s" : ""}?`)) return;
    const ws = getWs();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      showToast("Not connected to backend.", "warning");
      return;
    }
    for (const sk of targets) {
      ws.send(JSON.stringify({ type: "set_skill_enabled", name: sk.name, enabled }));
    }
    showToast(`${verb}d ${targets.length} skill${targets.length > 1 ? "s" : ""}`, "success", 2200);
  }
  root.querySelector(".cc-skill-bulk-on" )?.addEventListener("click", () => _bulkSet(true));
  root.querySelector(".cc-skill-bulk-off")?.addEventListener("click", () => _bulkSet(false));

  $refresh.addEventListener("click", () => {
    _send(getWs(), { type: "reload_skills" });
  });

  // ── Modal for skill body ─────────────────────────────────────────────────
  function showBodyModal(name, body, description, extras = {}) {
    $modalMount.innerHTML = "";
    const overlay = document.createElement("div");
    overlay.className = "cc-modal-overlay";
    const locLine = extras.location
      ? `<div style="font-size:10.5px;color:var(--cc-fg-dim);margin-top:4px;
                     font-family:monospace;overflow:hidden;text-overflow:ellipsis;
                     white-space:nowrap;cursor:text;user-select:all;"
              title="${escHtml(extras.location)}">📂 ${escHtml(extras.location)}</div>`
      : "";
    overlay.innerHTML = `
      <div class="cc-modal-card">
        <div class="cc-modal-header">
          <div style="min-width:0;flex:1;">
            <div style="font-weight:800;color:var(--cc-accent);font-family:monospace;
                        font-size:14px;overflow:hidden;text-overflow:ellipsis;
                        white-space:nowrap;">
              ${escHtml(name)}
            </div>
            <div style="font-size:11px;color:var(--cc-fg-muted);margin-top:2px;
                        line-height:1.4;">
              ${escHtml(description || "")}
            </div>
            ${locLine}
          </div>
          <button class="cc-icon-btn cc-modal-close" title="Close (Esc)">✕</button>
        </div>
        <div class="cc-modal-body cc-scroll">
          ${renderMarkdown(body || "")}
        </div>
      </div>
    `;
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) overlay.remove();
    });
    overlay.querySelector(".cc-modal-close").addEventListener("click", () => overlay.remove());
    // Esc-to-close
    const onKey = (e) => {
      if (e.key === "Escape") {
        overlay.remove();
        document.removeEventListener("keydown", onKey);
      }
    };
    document.addEventListener("keydown", onKey);
    $modalMount.appendChild(overlay);
  }

  /** Send a list_skills request when the WS is healthy. Returns true if sent. */
  function refresh() {
    const ws = getWs();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      _render();   // re-paint empty state with the right diagnostic
      return false;
    }
    _lastFetchAt = Date.now();
    ws.send(JSON.stringify({ type: "list_skills" }));
    console.debug("[ComfyClaw/skills] list_skills sent (state:", _wsState() + ")");
    return true;
  }

  // Auto-fetch on first ready + opportunistically poll while we still have
  // no manifest. Stops as soon as we receive one. Cheap (one ws send every
  // 2s while in the "fetching…" state).
  let _autoFetchTimer = null;
  function _startAutoFetch() {
    if (_autoFetchTimer) return;
    _autoFetchTimer = setInterval(() => {
      if (_gotManifest) {
        clearInterval(_autoFetchTimer);
        _autoFetchTimer = null;
        return;
      }
      refresh();
    }, 2000);
  }
  _startAutoFetch();
  // Try once on next tick too, so we don't have to wait for the first
  // 2-second interval tick if the WS is already up.
  setTimeout(refresh, 100);

  // Public API the panel uses to feed it server messages
  return {
    root,
    /** Fired on load + when the tab becomes active. */
    refresh,
    onMessage(msg) {
      if (msg.type === "skills_manifest") {
        _manifest = msg.skills || [];
        _gotManifest = true;
        console.debug("[ComfyClaw/skills] manifest received:",
                      _manifest.length, "skill(s)");
        _render();
        // Stop the watchdog poller as soon as the server has answered.
        if (_autoFetchTimer) {
          clearInterval(_autoFetchTimer);
          _autoFetchTimer = null;
        }
      } else if (msg.type === "skill_body") {
        showBodyModal(msg.name, msg.body, msg.description, {
          location: msg.location || "",
          license:  msg.license  || "",
        });
      } else if (msg.type === "skill_import_result") {
        if (msg.ok) {
          showToast(`Skill ${msg.action || "imported"}: ${msg.name}`, "success", 2500);
          // Manifest is auto-pushed by the server, no extra fetch needed.
        }
      } else if (msg.type === "skill_error") {
        console.warn("[ComfyClaw/skills] skill_error:", msg.error);
        showToast(msg.error || "Skill error", "error", 4500);
      }
    },
  };
}
