/** Lightweight Markdown renderer — no external deps. */

export function renderMarkdown(raw) {
  if (!raw) return "";

  // Extract fenced code blocks first so their content isn't processed
  const blocks = [];
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
