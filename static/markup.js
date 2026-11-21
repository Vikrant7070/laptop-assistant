/* ---------------- safe markup ----------------
   Every piece of markup this page builds is an html`...` template: each ${value} in it is escaped, unless it is
   itself an html`...` result (or a list of them), so a name or message from the server can never become markup.
   put() is the only place that sets innerHTML; el() builds one element without any markup at all.
   Shared by the chat page (nova.js) and the Knowledge page (knowledge.js). */
class Html { constructor(s) { this.s = s; } }
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const markup = v => v instanceof Html ? v.s : Array.isArray(v) ? v.map(markup).join("") : v === false || v == null ? "" : esc(v);
const html = (strings, ...vals) => new Html(strings.reduce((out, s, i) => out + s + (i < vals.length ? markup(vals[i]) : ""), ""));
function put(node, content) { node.innerHTML = markup(content); return node; }
function el(tag, props = {}, ...kids) { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; }
