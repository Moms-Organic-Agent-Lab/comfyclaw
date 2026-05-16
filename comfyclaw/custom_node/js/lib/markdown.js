/** Lightweight Markdown renderer — no external deps. */

export function renderMarkdown(raw) {
  if (!raw) return "";

  // Single placeholder pool for anything that's already-rendered HTML and
  // must survive the final `\n -> <br>` substitution (fenced code blocks
  // and GFM tables both land here).
  const blocks = [];

  // 1) Fenced code blocks.
  let text = raw.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const idx = blocks.length;
    const escaped = code.trim()
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const langLabel = lang
      ? `<span style="color:var(--cc-fg-dim);font-size:10px;font-family:monospace;">${lang}</span>`
      : "";
    const raw64 = btoa(unescape(encodeURIComponent(code.trim())));
    blocks.push(
      `<div class="cc-code-block" style="position:relative;margin:6px 0;">`
      + (langLabel ? `<div style="padding:4px 10px 2px;background:#11111b;border-radius:6px 6px 0 0;">${langLabel}</div>` : "")
      + `<pre style="background:#181825;border-radius:${lang ? "0 0 6px 6px" : "6px"};padding:8px 10px;`
      + `overflow-x:auto;margin:0;font-size:11px;line-height:1.5;">`
      + `<code style="font-family:monospace;">${escaped}</code></pre>`
      + `<button class="cc-copy-btn" data-b64="${raw64}">Copy</button>`
      + `</div>`
    );
    return `\x00BLK${idx}\x00`;
  });

  // 2) GFM tables. Done before the global escape + inline transforms below,
  //    because the table extractor needs to see raw `|` characters and
  //    applies its own per-cell escape + inline render via _renderInline.
  text = _extractTables(text, blocks);

  text = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  text = text.replace(/`([^`\n]+)`/g,
    `<code style="background:var(--cc-surface-2);border-radius:3px;padding:1px 5px;`
    + `font-size:11px;font-family:monospace;">$1</code>`);

  text = text.replace(/\*\*\*(.*?)\*\*\*/g, "<strong><em>$1</em></strong>");
  text = text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__(.*?)__/g, "<strong>$1</strong>");
  text = text.replace(/\*(.*?)\*/g, "<em>$1</em>");

  text = text.replace(/^### (.+)/gm,
    `<div style="font-weight:700;color:var(--cc-accent);margin:6px 0 2px;font-size:12px;">$1</div>`);
  text = text.replace(/^## (.+)/gm,
    `<div style="font-weight:700;color:var(--cc-accent);margin:8px 0 2px;font-size:13px;">$1</div>`);
  text = text.replace(/^# (.+)/gm,
    `<div style="font-weight:700;color:var(--cc-accent);margin:10px 0 4px;font-size:14px;">$1</div>`);

  text = text.replace(/^[ \t]*[*\-] (.+)/gm,
    `<div style="padding-left:14px;margin:1px 0;">• $1</div>`);
  text = text.replace(/^[ \t]*\d+\. (.+)/gm,
    `<div style="padding-left:14px;margin:1px 0;">$&</div>`);

  text = text.replace(/^---+$/gm,
    `<hr style="border:none;border-top:1px solid var(--cc-border);margin:6px 0;">`);

  text = text.replace(/\n/g, "<br>");
  text = text.replace(/\x00BLK(\d+)\x00/g, (_, i) => blocks[parseInt(i)]);
  return text;
}

// ─────────────────────────────────────────────────────────────────────────────
// GFM table support
// ─────────────────────────────────────────────────────────────────────────────

/** Escape + run inline transforms (code / bold / italic) on a table cell. */
function _renderInline(s) {
  let t = s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  t = t.replace(/`([^`\n]+)`/g,
    `<code style="background:var(--cc-surface-2);border-radius:3px;padding:1px 5px;`
    + `font-size:11px;font-family:monospace;">$1</code>`);
  t = t.replace(/\*\*\*(.*?)\*\*\*/g, "<strong><em>$1</em></strong>");
  t = t.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/__(.*?)__/g, "<strong>$1</strong>");
  t = t.replace(/\*(.*?)\*/g, "<em>$1</em>");
  return t;
}

/**
 * Walk lines; whenever a `| h1 | h2 |` header is followed by a
 * `|---|---|` (or `:---:` / `:---` / `---:` alignment) separator, fold the
 * rest of the contiguous `|`-piped block into one `<table>` and push the
 * rendered HTML onto `blocks`. Returns the text with `\x00BLK${idx}\x00`
 * placeholders standing in for each captured table.
 *
 * Requires at least two columns, which is enough to avoid clashing with
 * the existing `^---+$` horizontal-rule rule (a single `---` line).
 */
function _extractTables(text, blocks) {
  const lines = text.split("\n");
  // Separator: `|---|---|`, `|:--:|:--|--:|`, with optional outer pipes
  // and surrounding whitespace. Demands ≥2 dash-runs so it can't fire on
  // a horizontal-rule line.
  const sepRe =
    /^[ \t]*\|?[ \t]*:?-{2,}:?(?:[ \t]*\|[ \t]*:?-{2,}:?)+[ \t]*\|?[ \t]*$/;

  const splitCells = (line) => {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|"))   s = s.slice(0, -1);
    return s.split("|").map((c) => c.trim());
  };

  const parseAligns = (sep) =>
    sep.trim().replace(/^\|/, "").replace(/\|$/, "")
      .split("|").map((c) => {
        const t = c.trim();
        const l = t.startsWith(":"), r = t.endsWith(":");
        if (l && r) return "center";
        if (r)      return "right";
        if (l)      return "left";
        return null;
      });

  const out = [];
  let i = 0;
  while (i < lines.length) {
    const header = lines[i];
    const sep    = lines[i + 1];
    const looksLikeHeader = header && /\|/.test(header);
    const looksLikeSep    = sep && sepRe.test(sep);

    if (looksLikeHeader && looksLikeSep) {
      const rows = [];
      let j = i + 2;
      while (j < lines.length && /\|/.test(lines[j]) && lines[j].trim() !== "") {
        rows.push(lines[j]);
        j++;
      }

      const aligns = parseAligns(sep);
      const cellHtml = (txt, k, isTh) => {
        const align = aligns[k];
        const styles = [
          "padding:5px 9px",
          "border:1px solid var(--cc-border)",
          "vertical-align:top",
        ];
        if (align) styles.push(`text-align:${align}`);
        if (isTh)  styles.push("background:var(--cc-surface-2)",
                               "font-weight:700",
                               "color:var(--cc-fg)");
        else       styles.push("color:var(--cc-fg)");
        const tag = isTh ? "th" : "td";
        return `<${tag} style="${styles.join(";")}">${_renderInline(txt)}</${tag}>`;
      };

      let html =
        `<div class="cc-md-table-wrap" style="overflow-x:auto;margin:8px 0;">`
        + `<table class="cc-md-table" style="border-collapse:collapse;`
        + `font-size:11.5px;line-height:1.45;min-width:0;">`;
      html += `<thead><tr>`;
      splitCells(header).forEach((c, k) => { html += cellHtml(c, k, true); });
      html += `</tr></thead><tbody>`;
      rows.forEach((r) => {
        html += `<tr>`;
        splitCells(r).forEach((c, k) => { html += cellHtml(c, k, false); });
        html += `</tr>`;
      });
      html += `</tbody></table></div>`;

      const idx = blocks.length;
      blocks.push(html);
      out.push(`\x00BLK${idx}\x00`);
      i = j;
    } else {
      out.push(lines[i]);
      i++;
    }
  }
  return out.join("\n");
}
