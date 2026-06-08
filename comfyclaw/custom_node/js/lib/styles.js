/**
 * Shared design tokens, animations, and utility classes injected once at
 * startup.  Every other module reads colors / spacing through CSS variables;
 * when an inline style still needs the literal value we use ``var(--cc-*)``
 * so theming stays centralized.
 *
 * Use these classes instead of duplicating inline style strings:
 *
 *   .cc-row / .cc-row-tight    — flex row used for form fields, headers
 *   .cc-label                  — small uppercase section label
 *   .cc-pill                   — rounded pill badge (info / chip)
 *   .cc-status-pill            — generate-status indicator
 *   .cc-empty                  — empty-state container
 *   .cc-icon-btn               — tiny header / inline icon button
 *   .cc-progress / .cc-progress-bar — slim progress strip
 *   .cc-divider                — subtle horizontal rule
 *   .cc-modal-overlay / .cc-modal-card — modal scaffolding
 *   .cc-tab-pill (with .cc-tab-pill-dot) — sub-info attached to a tab button
 */

const CSS = `
/* ── Dark theme (default) ────────────────────────────────────────────── */
:root,
[data-cc-theme="dark"] {
  --cc-bg:        #1e1e2e;
  --cc-surface:   #25253a;
  --cc-surface-2: #313244;
  --cc-surface-tint: rgba(37,37,58,0.4);
  --cc-border:    #45475a;
  --cc-fg:        #cdd6f4;
  --cc-fg-muted:  #a6adc8;
  --cc-fg-dim:    #6c7086;
  --cc-fg-faint:  #45475a;
  --cc-accent:    #cba6f7;
  --cc-accent-soft: rgba(203,166,247,0.13);
  --cc-accent-soft-2: var(--cc-accent-soft-2);
  --cc-accent-blue:   #89b4fa;
  --cc-accent-green:  #a6e3a1;
  --cc-accent-yellow: #f9e2af;
  --cc-accent-red:    #f38ba8;
  --cc-accent-orange: #fab387;
  --cc-shadow:    0 6px 32px rgba(0,0,0,0.5);
  --cc-shadow-sm: 0 2px 8px rgba(0,0,0,0.35);
  --cc-radius:    14px;
  --cc-radius-sm: 8px;
  --cc-radius-xs: 6px;
  --cc-space-1:   4px;
  --cc-space-2:   6px;
  --cc-space-3:   8px;
  --cc-space-4:   10px;
  --cc-space-5:   14px;
  --cc-ease:      cubic-bezier(0.2, 0.8, 0.4, 1);
}

/* ── Light theme (Cursor-style) ──────────────────────────────────────── */
[data-cc-theme="light"] {
  --cc-bg:        #fbfbfd;
  --cc-surface:   #f1f1f5;
  --cc-surface-2: #e7e7ec;
  --cc-surface-tint: rgba(231,231,236,0.55);
  --cc-border:    #d4d4dc;
  --cc-fg:        #1f2024;
  --cc-fg-muted:  #4b4d57;
  --cc-fg-dim:    #7a7d8a;
  --cc-fg-faint:  #c8c8d0;
  --cc-accent:    #6f43c9;
  --cc-accent-soft: rgba(111,67,201,0.10);
  --cc-accent-soft-2: rgba(111,67,201,0.16);
  --cc-accent-blue:   #2f6feb;
  --cc-accent-green:  #2da44e;
  --cc-accent-yellow: #b7791f;
  --cc-accent-red:    #cf222e;
  --cc-accent-orange: #d97706;
  --cc-shadow:    0 6px 28px rgba(15,17,26,0.12);
  --cc-shadow-sm: 0 1px 4px rgba(15,17,26,0.08);
}

@keyframes cc-spin   { to { transform: rotate(360deg); } }
@keyframes cc-pulse  { 0%,100%{opacity:1} 50%{opacity:0.4} }
@keyframes cc-fadein { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }
@keyframes cc-blink  { 0%,100%{opacity:1} 50%{opacity:0.3} }
@keyframes cc-slide-up { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
@keyframes cc-slide-up-card {
  from { opacity: 0; transform: translateY(14px) scale(0.98); }
  to   { opacity: 1; transform: none; }
}
@keyframes cc-progress-stripes {
  from { background-position: 0 0; }
  to   { background-position: 28px 0; }
}

.cc-spin    {
  animation: cc-spin 1s linear infinite;
  display: inline-block;
  transform-origin: 50% 50%;
  vertical-align: middle;
}
.cc-pulse   { animation: cc-pulse 1.6s ease-in-out infinite; }
.cc-fadein  { animation: cc-fadein 0.18s ease-out forwards; }
.cc-entry-in { animation: cc-fadein 0.15s ease-out; }

/* Border-ring spinner — perfectly circular geometry, so rotation is
   visually stable (text glyphs like ⟳ wobble because their visual
   center doesn't match the box center). Use as a span/div. */
.cc-spin-ring {
  display: inline-block;
  width: 14px; height: 14px;
  border: 2px solid var(--cc-fg-faint);
  border-top-color: var(--cc-accent);
  border-radius: 50%;
  box-sizing: border-box;
  vertical-align: middle;
  animation: cc-spin 0.85s linear infinite;
  transform-origin: 50% 50%;
  flex-shrink: 0;
}
.cc-spin-ring-sm { width: 12px; height: 12px; border-width: 2px; }
.cc-spin-ring-lg { width: 18px; height: 18px; border-width: 2.5px; }

/* Custom scrollbar everywhere we opt-in */
#comfyclaw-think-log::-webkit-scrollbar,
.cc-scroll::-webkit-scrollbar { width:6px; height:6px; }
#comfyclaw-think-log::-webkit-scrollbar-track,
.cc-scroll::-webkit-scrollbar-track { background:transparent; }
#comfyclaw-think-log::-webkit-scrollbar-thumb,
.cc-scroll::-webkit-scrollbar-thumb {
  background: var(--cc-border);
  border-radius: 3px;
}
#comfyclaw-think-log::-webkit-scrollbar-thumb:hover,
.cc-scroll::-webkit-scrollbar-thumb:hover { background: var(--cc-fg-dim); }

/* Code block w/ copy button (used by markdown.js) */
.cc-code-block { position:relative; }
.cc-copy-btn {
  position:absolute; top:6px; right:6px;
  background:var(--cc-border); border:none; color:var(--cc-fg);
  border-radius:5px; padding:3px 10px; font-size:12px; cursor:pointer;
  opacity:0; transition:opacity 0.15s; font-family:system-ui,sans-serif;
}
.cc-code-block:hover .cc-copy-btn { opacity:1; }
.cc-log-entry { animation: cc-fadein 0.15s ease-out; }
.cc-log-entry:hover .cc-msg-copy { opacity: 1 !important; }
.cc-msg-copy { opacity: 0; transition: opacity 0.15s, background 0.15s, color 0.15s; }
.cc-msg-copy:hover { background: var(--cc-surface); color: var(--cc-fg) !important; }

/* ── Tabs ─────────────────────────────────────────────────────────────── */
.cc-tab-button {
  position: relative; flex: 1; padding: 11px 10px; border: none;
  background: transparent; color: var(--cc-fg-dim); cursor: pointer;
  font: inherit; font-size: 13px; font-weight: 600;
  border-bottom: 2px solid transparent;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  letter-spacing: 0.2px;
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  user-select: none;
}
.cc-tab-button:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 2px var(--cc-accent-soft-2);
}
.cc-tab-button:hover {
  color: var(--cc-fg-muted);
  background: linear-gradient(to bottom, transparent 0%, var(--cc-accent-soft) 100%);
}
.cc-tab-button.cc-tab-active {
  color: var(--cc-accent); border-bottom-color: var(--cc-accent);
  background: linear-gradient(to bottom, transparent 0%, var(--cc-accent-soft) 100%);
}
.cc-tab-pill {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10px; font-weight: 700;
  background: var(--cc-surface-2); color: var(--cc-fg-muted);
  border-radius: 9px; padding: 2px 7px; margin-left: 1px;
  font-variant-numeric: tabular-nums;
}
.cc-tab-active .cc-tab-pill {
  background: var(--cc-accent-soft-2); color: var(--cc-accent);
}
.cc-tab-pill-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--cc-accent-green);
  animation: cc-pulse 1.4s ease-in-out infinite;
  display: inline-block;
}

/* ── Chips (for backend picker, quick-prompts, etc.) ──────────────────── */
.cc-chip {
  display:inline-flex; align-items:center; gap:5px;
  padding:4px 11px; border-radius:20px;
  border:1px solid var(--cc-border); background:transparent;
  color:var(--cc-fg-muted); cursor:pointer; font-size:12px;
  font-weight:600; transition:all 0.15s; white-space:nowrap;
  line-height: 1.3;
}
.cc-chip:hover {
  border-color:var(--cc-accent); color:var(--cc-accent);
  background:var(--cc-accent-soft);
}
.cc-chip:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
  border-color: var(--cc-accent);
}
.cc-chip.cc-chip-active {
  border-color:var(--cc-accent); color:var(--cc-accent);
  background:var(--cc-accent-soft-2);
}

/* ── Buttons (labeled, with text) ─────────────────────────────────────── */
.cc-btn {
  border: none; border-radius: var(--cc-radius-sm);
  padding: 9px 14px; cursor: pointer;
  font-family: inherit; font-size: 13px; font-weight: 600;
  transition: filter 0.15s, transform 0.05s, background 0.15s, box-shadow 0.15s;
  display: inline-flex; align-items: center; justify-content: center;
  gap: 6px; line-height: 1;
  user-select: none;
}
.cc-btn:hover { filter: brightness(1.08); }
.cc-btn:active { transform: scale(0.97); }
.cc-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
}
.cc-btn[disabled] { opacity:0.45; cursor:not-allowed; filter:none; }
.cc-btn-primary { background: var(--cc-accent-green); color: var(--cc-bg); }
.cc-btn-secondary {
  background: var(--cc-surface-2); color: var(--cc-fg);
  border: 1px solid var(--cc-border);
}
.cc-btn-secondary:hover { border-color: var(--cc-fg-dim); }
.cc-btn-danger { background: var(--cc-accent-red); color: var(--cc-bg); }
.cc-btn-accent { background: var(--cc-accent); color: var(--cc-bg); }
.cc-btn-info { background: var(--cc-accent-blue); color: var(--cc-bg); }
.cc-btn-warn { background: var(--cc-accent-yellow); color: var(--cc-bg); }

/* Icon-only square button — used for ⚙ ▾ × 🗑 etc.
 *
 * Single source of truth for icon-button sizing. Three sizes:
 *   .cc-icon-btn          → 36×36 / 20px (default, used in panel headers)
 *   .cc-icon-btn.cc-icon-btn-sm → 32×32 / 18px (denser toolbars / lists)
 *   .cc-icon-btn.cc-icon-btn-xs → 28×28 / 15px (compact inline clusters)
 *
 * Hover fills with surface-2 + visible border. Active scales 0.94 for tactile
 * feedback. Focus shows an accent-soft ring for keyboard accessibility.
 */
.cc-icon-btn {
  background: transparent;
  border: 1px solid transparent;
  color: var(--cc-fg-muted);
  cursor: pointer;
  width: 36px; height: 36px; padding: 0;
  border-radius: var(--cc-radius-sm);
  font-size: 20px; font-weight: 500; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  transition: background 0.15s, color 0.15s, border-color 0.15s, transform 0.05s;
  user-select: none;
  font-family: inherit;
}
.cc-icon-btn:hover {
  color: var(--cc-fg);
  background: var(--cc-surface-2);
  border-color: var(--cc-border);
}
.cc-icon-btn:active { transform: scale(0.94); }
.cc-icon-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
  border-color: var(--cc-accent);
}
.cc-icon-btn.cc-icon-btn-sm { width: 32px; height: 32px; font-size: 18px; }
.cc-icon-btn.cc-icon-btn-xs { width: 28px; height: 28px; font-size: 15px; }
.cc-icon-btn[disabled] { opacity: 0.4; cursor: not-allowed; }
.cc-icon-btn[disabled]:hover {
  background: transparent; border-color: transparent; color: var(--cc-fg-muted);
}
/* Subtle accent treatment for primary header buttons (settings, etc.) */
.cc-icon-btn.cc-icon-btn-accent { color: var(--cc-accent); }
.cc-icon-btn.cc-icon-btn-accent:hover { color: var(--cc-accent); filter: brightness(1.1); }

/* ── Inputs ───────────────────────────────────────────────────────────── */
.cc-input,
.cc-select {
  width:100%; box-sizing:border-box;
  background: var(--cc-surface-2); color: var(--cc-fg);
  border: 1px solid var(--cc-border); border-radius: var(--cc-radius-sm);
  padding: 7px 10px; font-size: 12px; font-family: inherit;
  outline: none; transition: border-color 0.15s, box-shadow 0.15s;
}
.cc-input:focus,
.cc-select:focus,
.cc-textarea:focus {
  border-color: var(--cc-accent);
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
}
.cc-textarea {
  width:100%; box-sizing:border-box;
  background: var(--cc-surface-2); color: var(--cc-fg);
  border: 1px solid var(--cc-border); border-radius: var(--cc-radius-sm);
  padding: 8px 10px; font-size: 13px; font-family: inherit;
  resize: vertical; outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.cc-segment-row {
  display:flex; gap:4px;
  background:var(--cc-bg);
  padding:3px;
  border-radius:10px;
  border:1px solid var(--cc-border);
}
.cc-segment-btn {
  flex:1; border:none; background:transparent;
  color:var(--cc-fg-dim); cursor:pointer;
  padding:7px 6px; border-radius:7px;
  font-size:12px; font-weight:700; letter-spacing:0;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
  display:flex; align-items:center; justify-content:center; gap:5px;
  font-family:inherit;
  line-height:1.2;
  min-width:0;
}
.cc-segment-btn:hover {
  color:var(--cc-fg);
  background:var(--cc-surface-tint);
}
.cc-segment-btn[data-active="1"] {
  background:var(--cc-surface-2);
  color:var(--cc-accent);
  box-shadow:var(--cc-shadow-sm);
}
.cc-segment-btn:focus-visible {
  outline:none;
  box-shadow:0 0 0 2px var(--cc-accent-soft-2);
}

/* ── Layout helpers ───────────────────────────────────────────────────── */
.cc-row {
  display:flex; align-items:center; gap: var(--cc-space-3);
}
.cc-row-tight { display:flex; align-items:center; gap: var(--cc-space-2); }
.cc-col { display:flex; flex-direction:column; gap: var(--cc-space-2); }
.cc-label {
  display:block; color: var(--cc-fg-dim);
  font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
  text-transform: uppercase;
}
.cc-divider {
  height: 1px; background: var(--cc-border);
  border: 0; margin: var(--cc-space-3) 0;
}

/* ── Cards ────────────────────────────────────────────────────────────── */
.cc-card {
  background: var(--cc-surface-2);
  border-radius: 10px;
  padding: 12px 14px;
}

/* ── Pills / badges ───────────────────────────────────────────────────── */
.cc-pill {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 10px; font-weight: 700;
  padding: 1px 6px; border-radius: 9px;
  background: var(--cc-surface-2); color: var(--cc-fg-muted);
  border: 1px solid var(--cc-border);
  letter-spacing: 0.3px; text-transform: uppercase;
  white-space: nowrap;
}
.cc-pill-mono { font-family: monospace; text-transform: none; letter-spacing: 0; }
.cc-pill-tag {
  font-size: 9px; padding: 1px 5px; border-radius: 3px;
  text-transform: uppercase; letter-spacing: 0.4px;
  font-weight: 700; line-height: 1.4;
}

/* Status pill displayed under the Generate button */
.cc-status-pill {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--cc-space-3);
  margin-top: var(--cc-space-3);
  padding: 6px 10px;
  background: var(--cc-surface-2);
  border-radius: var(--cc-radius-sm);
  font-size: 12px; color: var(--cc-fg-muted);
  border: 1px solid var(--cc-border);
  border-left-width: 3px; border-left-style: solid;
  border-left-color: var(--cc-fg-dim);
  transition: border-left-color 0.18s, color 0.18s;
}
.cc-status-pill[data-state="running"]   { border-left-color: var(--cc-accent-blue); color: var(--cc-accent-blue); }
.cc-status-pill[data-state="verifying"] { border-left-color: var(--cc-accent-yellow); color: var(--cc-accent-yellow); }
.cc-status-pill[data-state="repairing"] { border-left-color: var(--cc-accent-orange); color: var(--cc-accent-orange); }
.cc-status-pill[data-state="complete"]  { border-left-color: var(--cc-accent-green); color: var(--cc-accent-green); }
.cc-status-pill[data-state="error"]     { border-left-color: var(--cc-accent-red); color: var(--cc-accent-red); }
.cc-status-pill[data-state="dry_run_done"] { border-left-color: var(--cc-accent-yellow); color: var(--cc-accent-yellow); }
.cc-status-pill[data-state="evolving_skills"] { border-left-color: var(--cc-accent-blue); color: var(--cc-accent-blue); }
.cc-status-pill[data-state="awaiting_skill_approval"] { border-left-color: var(--cc-accent); color: var(--cc-accent); }

/* ── Score bar (used by scoreboard.js + history_panel.js) ─────────────── */
.cc-score-bar-bg {
  background: var(--cc-bg);
  border-radius: 4px;
  height: 6px;
  overflow: hidden;
  border: 1px solid var(--cc-border);
}
.cc-score-bar-fg {
  height: 100%; border-radius: 4px;
  transition: width 0.4s var(--cc-ease);
}

/* ── Generate progress bar (under the running button) ─────────────────── */
.cc-progress {
  height: 4px; width: 100%;
  background: var(--cc-surface-2);
  border-radius: 4px; overflow: hidden;
  margin-top: var(--cc-space-2);
  border: 1px solid var(--cc-border);
}
.cc-progress-bar {
  height: 100%;
  background: repeating-linear-gradient(
    -45deg,
    var(--cc-accent-blue) 0 12px,
    rgba(137,180,250,0.55) 12px 24px
  );
  background-size: 28px 100%;
  animation: cc-progress-stripes 0.9s linear infinite;
  width: 100%;
}

/* ── Empty state ──────────────────────────────────────────────────────── */
.cc-empty {
  text-align: center; padding: 40px 20px;
  color: var(--cc-fg-dim); font-size: 12px; line-height: 1.6;
  display: flex; flex-direction: column; align-items: center; gap: 10px;
}
.cc-empty-icon {
  font-size: 36px; opacity: 0.3; line-height: 1;
}
.cc-empty-title {
  font-size: 13px; font-weight: 700; color: var(--cc-fg-muted);
}
.cc-empty-actions {
  display: flex; gap: 6px; flex-wrap: wrap; justify-content: center;
  margin-top: 4px;
}

/* ── Modal ────────────────────────────────────────────────────────────── */
.cc-modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.55);
  z-index: 99999; display: flex; align-items: center; justify-content: center;
  backdrop-filter: blur(2px);
  animation: cc-fadein 0.16s ease-out;
}
.cc-modal-card {
  background: var(--cc-bg); color: var(--cc-fg);
  width: min(720px, 92vw); max-height: 84vh;
  border-radius: var(--cc-radius);
  box-shadow: var(--cc-shadow);
  display: flex; flex-direction: column;
  border: 1px solid var(--cc-border);
  animation: cc-slide-up-card 0.22s var(--cc-ease);
}
.cc-modal-header {
  padding: 12px 16px; display: flex; align-items: center;
  justify-content: space-between; gap: 10px;
  border-bottom: 1px solid var(--cc-border);
}
.cc-modal-body {
  overflow-y: auto; padding: 14px 18px; font-size: 12px; line-height: 1.6;
  flex: 1; min-height: 0;
}

/* ── Misc transitions for legacy DOM ──────────────────────────────────── */
#comfyclaw-gen-prompt,
#comfyclaw-think-input { transition: border-color 0.15s, box-shadow 0.15s; }
#comfyclaw-gen-prompt:focus,
#comfyclaw-think-input:focus {
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
}

/* Light theme: brighter primary buttons need readable text */
[data-cc-theme="light"] .cc-btn-primary,
[data-cc-theme="light"] .cc-btn-danger,
[data-cc-theme="light"] .cc-btn-accent,
[data-cc-theme="light"] .cc-btn-info,
[data-cc-theme="light"] .cc-btn-warn { color: #ffffff; }

/* ── Dock modes ──────────────────────────────────────────────────────── */
/* Mounted inside ComfyUI's native sidebar host */
#comfyclaw-panel[data-dock="comfy-sidebar"] {
  position: relative !important;
  top: auto !important;
  right: auto !important;
  left: auto !important;
  bottom: auto !important;
  width: 100% !important;
  height: 100% !important;
  max-height: none !important;
  border: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
}
#comfyclaw-panel[data-dock="comfy-sidebar"] #comfyclaw-gen-header {
  border-radius: 0 !important;
  cursor: default;
}
#comfyclaw-panel[data-dock="comfy-sidebar"] #comfyclaw-close-btn,
#comfyclaw-panel[data-dock="comfy-sidebar"] .cc-panel-resize-corner,
#comfyclaw-panel[data-dock="comfy-sidebar"] .cc-panel-resize-edge { display: none !important; }

/* When ComfyClaw owns a native sidebar slot, hide the floating edge handle. */
body[data-cc-has-native-sidebar="1"] #comfyclaw-edge-handle { display: none !important; }

/* Sidebar tab icon (registered via PrimeIcons class fallback). */
.cc-icon-comfyclaw::before {
  content: "🐾";
  font-style: normal;
  font-size: 20px;
  line-height: 1;
}

#comfyclaw-panel[data-dock="sidebar"] {
  top: 0 !important;
  right: 0 !important;
  left: auto !important;
  bottom: 0 !important;
  height: 100vh !important;
  max-height: 100vh !important;
  border-radius: 0 !important;
  border-top: none;
  border-right: none;
  border-bottom: none;
  box-shadow: -8px 0 24px rgba(0,0,0,0.18);
}
#comfyclaw-panel[data-dock="sidebar"] #comfyclaw-gen-header {
  border-radius: 0 !important;
  cursor: default;
}
#comfyclaw-panel[data-dock="sidebar"] .cc-panel-resize-corner { display: none; }
#comfyclaw-panel[data-dock="sidebar"] .cc-panel-resize-edge   { display: block; }

#comfyclaw-panel[data-dock="float"] .cc-panel-resize-corner { display: block; }
#comfyclaw-panel[data-dock="float"] .cc-panel-resize-edge   { display: none; }

/* Left-edge resize strip (sidebar mode only) */
.cc-panel-resize-edge {
  position: absolute; top: 0; left: 0; width: 4px; height: 100%;
  cursor: ew-resize; z-index: 3; background: transparent;
  transition: background 0.15s;
}
.cc-panel-resize-edge:hover { background: var(--cc-accent-soft-2); }

/* ── Edge handle (shown when panel is hidden) ────────────────────────── */
#comfyclaw-edge-handle {
  position: fixed; top: 50%; right: 0; transform: translateY(-50%);
  z-index: 9997;
  width: 30px; height: 64px;
  background: var(--cc-surface);
  border: 1px solid var(--cc-border);
  border-right: none;
  border-radius: 10px 0 0 10px;
  box-shadow: -2px 2px 10px rgba(0,0,0,0.18);
  color: var(--cc-accent);
  display: none;
  align-items: center; justify-content: center;
  cursor: pointer;
  font-size: 20px;
  transition: transform 0.15s, background 0.15s;
}
#comfyclaw-edge-handle:hover {
  background: var(--cc-surface-2);
  transform: translateY(-50%) translateX(-2px);
}

/* ── Sticky action bar (Generate / Stop / Audit — always visible) ─── */
#comfyclaw-action-bar {
  flex-shrink: 0;
  padding: 10px 14px;
  background: var(--cc-surface);
  border-top: 1px solid var(--cc-border);
  border-bottom: 1px solid var(--cc-border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
#comfyclaw-action-bar .cc-action-row {
  display: flex; gap: 6px;
}
#comfyclaw-action-bar .cc-btn { font-size: 13px; padding: 10px 12px; }
#comfyclaw-action-bar .cc-action-primary { flex: 1; display: flex; }
#comfyclaw-action-bar .cc-action-primary .cc-btn { flex: 1; }

/* Theme + dock toggle buttons — inherit cc-icon-btn sizing */
.cc-header-toggle { font-size: 20px; line-height: 1; }

/* ── Cursor-style composer card ──────────────────────────────────────── */
.cc-composer-card {
  display: flex; flex-direction: column;
  background: var(--cc-surface-2);
  border: 1px solid var(--cc-border);
  border-radius: 12px;
  padding: 8px 10px 6px;
  gap: 6px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.cc-composer-card:focus-within {
  border-color: var(--cc-accent);
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
}
.cc-composer-card textarea {
  border: none !important;
  background: transparent !important;
  padding: 4px 2px !important;
  resize: none;
  outline: none;
  font-size: 13px;
  color: var(--cc-fg);
  font-family: inherit;
  line-height: 1.45;
  max-height: 160px;
  min-height: 22px;
  overflow-y: auto;
}
.cc-composer-card textarea:focus { box-shadow: none !important; }
.cc-composer-toolbar {
  display: flex; align-items: center; gap: 6px;
  flex-wrap: wrap;
  padding-top: 4px;
}
.cc-composer-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 10px; border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--cc-fg-muted);
  font-size: 12.5px; font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
  white-space: nowrap;
  max-width: 200px; overflow: hidden; text-overflow: ellipsis;
  line-height: 1.2;
}
.cc-composer-chip:hover {
  background: var(--cc-surface);
  border-color: var(--cc-border);
  color: var(--cc-fg);
}
.cc-composer-chip:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
  border-color: var(--cc-accent);
}
.cc-composer-chip .cc-chip-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--cc-accent);
  flex-shrink: 0;
}
.cc-composer-chip .cc-chip-chev {
  font-size: 11px; opacity: 0.55; margin-left: 1px;
}
.cc-composer-btn {
  width: 36px; height: 36px; border-radius: var(--cc-radius-sm);
  border: 1px solid var(--cc-border);
  background: var(--cc-surface);
  color: var(--cc-fg-muted);
  cursor: pointer;
  font-size: 18px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  transition: background 0.15s, border-color 0.15s, color 0.15s, transform 0.05s;
  font-family: inherit;
  flex-shrink: 0;
  user-select: none;
}
.cc-composer-btn:hover {
  background: var(--cc-surface-2);
  border-color: var(--cc-fg-dim);
  color: var(--cc-fg);
}
.cc-composer-btn:active { transform: scale(0.94); }
.cc-composer-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--cc-accent-soft-2);
}
.cc-composer-btn[disabled] { opacity: 0.4; cursor: not-allowed; }
.cc-composer-btn[disabled]:hover {
  background: var(--cc-surface);
  border-color: var(--cc-border);
  color: var(--cc-fg-muted);
}
.cc-composer-btn-primary {
  background: var(--cc-accent);
  border-color: var(--cc-accent);
  color: var(--cc-bg);
}
.cc-composer-btn-primary:hover { background: var(--cc-accent); filter: brightness(1.08); color: var(--cc-bg); }
.cc-composer-btn-run {
  background: var(--cc-accent-green);
  border-color: var(--cc-accent-green);
  color: var(--cc-bg);
}
[data-cc-theme="light"] .cc-composer-btn-run { color: #ffffff; }
.cc-composer-btn-run:hover { filter: brightness(1.08); }
.cc-composer-btn-stop {
  background: var(--cc-accent-red);
  border-color: var(--cc-accent-red);
  color: var(--cc-bg);
}
[data-cc-theme="light"] .cc-composer-btn-stop { color: #ffffff; }
.cc-composer-btn-stop:hover { filter: brightness(1.08); }
.cc-chip-icon { font-size: 15px; line-height: 1; }

/* Slim progress bar at the top edge of the composer */
.cc-composer-progress {
  margin: 0 0 4px;
  height: 3px;
  border: none;
  background: transparent;
}
.cc-composer-progress .cc-progress-bar { border-radius: 2px; }

/* Single-line status row inside the composer */
.cc-composer-status {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px;
  padding: 2px 2px 0;
  font-size: 11px; color: var(--cc-fg-muted);
}
.cc-composer-status[data-state="running"]   { color: var(--cc-accent-blue); }
.cc-composer-status[data-state="verifying"] { color: var(--cc-accent-yellow); }
.cc-composer-status[data-state="repairing"] { color: var(--cc-accent-orange); }
.cc-composer-status[data-state="complete"]  { color: var(--cc-accent-green); }
.cc-composer-status[data-state="error"]     { color: var(--cc-accent-red); }
.cc-composer-status[data-state="dry_run_done"] { color: var(--cc-accent-yellow); }
.cc-composer-status[data-state="evolving_skills"] { color: var(--cc-accent-blue); }
.cc-composer-status[data-state="awaiting_skill_approval"] { color: var(--cc-accent); }
.cc-composer-status span:first-child {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}

/* Sticky action bar is now redundant — the composer holds Run/Stop/Audit. */
#comfyclaw-action-bar { display: none !important; }

/* Popover for model picker (anchored to a chip) */
.cc-popover {
  position: fixed;
  z-index: 10100;
  min-width: 220px; max-width: 280px;
  background: var(--cc-bg);
  border: 1px solid var(--cc-border);
  border-radius: 10px;
  box-shadow: var(--cc-shadow);
  padding: 6px;
  display: none;
  font-size: 12px;
  animation: cc-fadein 0.12s ease-out;
}
.cc-popover[data-open="1"] { display: block; }
.cc-popover-section-label {
  font-size: 9px; font-weight: 700; letter-spacing: 0.5px;
  text-transform: uppercase; color: var(--cc-fg-dim);
  padding: 6px 8px 2px;
}
.cc-popover-item {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 8px; border-radius: 6px;
  cursor: pointer;
  color: var(--cc-fg);
  transition: background 0.1s;
}
.cc-popover-item:hover { background: var(--cc-surface-2); }
.cc-popover-item[data-active="1"] {
  background: var(--cc-accent-soft);
  color: var(--cc-accent);
}
.cc-popover-item .cc-popover-icon {
  width: 18px; font-size: 14px; text-align: center; opacity: 0.8;
}
`;

let _injected = false;
export function injectStyles() {
  if (_injected) return;
  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);
  _injected = true;
}
