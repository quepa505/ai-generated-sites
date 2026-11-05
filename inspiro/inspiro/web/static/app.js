/* Inspiro — frontend (vanilla JS, bez kroku budowania) */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const IDEA_ICONS = { app: "📱", game: "🎮", youtube: "📺", tool: "🛠️", content: "✍️", business: "💼", learning: "📚", other: "💡" };
const IDEA_LABELS = { app: "aplikacja", game: "gra", youtube: "YouTube", tool: "narzędzie", content: "treści", business: "biznes", learning: "nauka", other: "inne" };
const SOURCE_LABELS = { reddit: "Reddit", x: "X", manual: "Link" };
const JOB_LABELS = { sync: "Synchronizacja", analyze: "Analiza zakładek", ideas: "Generowanie pomysłów", auto: "Pełny cykl" };

const state = { itemsOffset: 0, itemsFilter: {}, suggStatus: "new", convId: null, sending: false };

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (opts.body && typeof opts.body !== "string") {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* brak treści */ }
  if (!res.ok) throw new Error((data && (data.detail?.[0]?.msg || data.detail)) || `HTTP ${res.status}`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), isErr ? 7000 : 3500);
}

const guard = (fn) => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message, true); } };

// ------------------------------------------------------------------ Markdown (minimalny, bezpieczny)
function md(src) {
  const blocks = [];
  let s = esc(src).replace(/```[\w-]*\n?([\s\S]*?)```/g, (_, code) => { blocks.push(code); return `\u0000${blocks.length - 1}\u0000`; });
  const inline = (t) => t
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\[#(\d+)\]/g, '<a href="#" class="itemref" data-item="$1">#$1</a>');
  const out = [];
  let list = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of s.split("\n")) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\u0000(\d+)\u0000$/))) { closeList(); out.push(`<pre><code>${blocks[+m[1]]}</code></pre>`); continue; }
    if ((m = line.match(/^#{1,2}\s+(.*)/))) { closeList(); out.push(`<h3>${inline(m[1])}</h3>`); continue; }
    if ((m = line.match(/^#{3,6}\s+(.*)/))) { closeList(); out.push(`<h4>${inline(m[1])}</h4>`); continue; }
    if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
    closeList();
    if (line.trim() === "") continue;
    if (/^-{3,}$/.test(line.trim())) { out.push("<hr>"); continue; }
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("\n").replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${blocks[+i]}</code>`);
}

// ------------------------------------------------------------------ routing
const views = ["pulpit", "zakladki", "pomysly", "projekty", "agent", "pamiec", "zrodla"];
function route() {
  const hash = location.hash.slice(1);
  const [name, query] = hash.split("?");
  const view = views.includes(name) ? name : "pulpit";
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  const params = new URLSearchParams(query || "");
  if (params.get("x_connected")) toast("Połączono z X ✓");
  if (params.get("x_error")) toast("X: " + params.get("x_error"), true);
  loaders[view]?.();
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------ wspólne fragmenty
const stars = (n) => n ? `<span class="stars" title="przydatność ${n}/5">${"★".repeat(n)}${"☆".repeat(5 - n)}</span>` : "";
const chips = (arr, cls = "") => (arr || []).map((t) => `<button class="chip ${cls}" data-search="${esc(t)}">${esc(t)}</button>`).join("");
const sourceBadge = (src) => `<span class="badge ${esc(src)}">${esc(SOURCE_LABELS[src] || src)}</span>`;
const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("pl-PL", { day: "numeric", month: "short", year: "numeric" }) : "";

function itemCard(it) {
  const status = it.status === "new" ? '<span class="badge status-new">do analizy</span>'
    : it.status === "error" ? `<span class="badge status-error" title="${esc(it.error)}">błąd analizy</span>` : "";
  return `<article class="item" data-id="${it.id}">
    <div class="item-top">${sourceBadge(it.source)}<span>${esc(it.context || "")}</span><span>${esc(it.author || "")}</span><span>${fmtDate(it.created_at || it.imported_at)}</span>${status}<span class="spacer"></span>${stars(it.usefulness)}</div>
    <div class="item-title">${esc(it.title || it.url || "(bez tytułu)")}</div>
    ${it.summary ? `<div class="item-summary">${esc(it.summary)}</div>` : ""}
    <div class="chips">${chips(it.topics)}${chips(it.technologies, "tech")}</div>
  </article>`;
}

function suggCard(s) {
  const icon = s.kind === "project_use" ? "🧩" : (IDEA_ICONS[s.idea_type] || "💡");
  const meta = s.kind === "project_use"
    ? `do projektu <b>${esc(s.project_name || "?")}</b>`
    : `${esc(IDEA_LABELS[s.idea_type] || "pomysł")}${s.project_name ? ` · rozwija <b>${esc(s.project_name)}</b>` : ""}`;
  const refs = (s.item_ids || []).map((id) => `<a href="#" class="itemref" data-item="${id}">#${id}</a>`).join(" ");
  const steps = (s.first_steps || []).length ? `<ol>${s.first_steps.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : "";
  const btn = (st, label) => s.status === st ? "" : `<button class="small" data-sugg="${s.id}" data-status="${st}">${label}</button>`;
  return `<div class="sugg">
    <div class="sugg-head"><span class="sugg-icon">${icon}</span><div><div class="sugg-title">${esc(s.title)}</div><div class="sugg-meta">${meta} · ${stars(s.score)}</div></div></div>
    <div class="sugg-body">${esc(s.body)}</div>
    ${s.why_you ? `<div class="sugg-why">${esc(s.why_you)}</div>` : ""}
    ${steps}
    <div class="sugg-foot"><span class="refs">${refs ? "na bazie: " + refs : ""}</span>
      ${btn("accepted", "👍 Biorę")}${btn("done", "✅ Zrobione")}${btn("dismissed", "✕")}
      <button class="small" data-ask="${s.id}">🤖 Rozwiń z agentem</button></div>
  </div>`;
}

// ------------------------------------------------------------------ PULPIT
async function loadDashboard() {
  const [st, sugg] = await Promise.all([api("/api/stats"), api("/api/suggestions?status=new")]);
  const card = (num, lbl, href) => `<a class="card" href="${href}"><div class="num">${num}</div><div class="lbl">${lbl}</div></a>`;
  $("#stat-cards").innerHTML = [
    card(st.items_total, `zakładek (${st.imported_last_7_days} w tym tygodniu)`, "#zakladki"),
    card(st.by_status.new || 0, "czeka na analizę", "#zakladki"),
    card(st.suggestions.new || 0, "nowych propozycji", "#pomysly"),
    card(st.projects, "projektów", "#projekty"),
  ].join("");
  const counted = (arr, cls) => arr.length ? arr.map(([t, n]) => `<button class="chip ${cls}" data-search="${esc(t)}">${esc(t)}<b>${n}</b></button>`).join("") : '<span class="muted small">Pojawią się po analizie zakładek.</span>';
  $("#top-topics").innerHTML = counted(st.top_topics, "");
  $("#top-tech").innerHTML = counted(st.top_technologies, "tech");
  $("#dash-suggestions").innerHTML = sugg.length ? sugg.slice(0, 6).map(suggCard).join("")
    : `<div class="empty">Brak nowych propozycji. Dodaj projekty i zakładki, potem kliknij „Wygeneruj pomysły”.</div>`;
}

// ------------------------------------------------------------------ ZAKŁADKI
async function loadItems(append = false) {
  if (!append) state.itemsOffset = 0;
  const params = new URLSearchParams({ ...state.itemsFilter, limit: 30, offset: state.itemsOffset });
  for (const [k, v] of [...params]) if (!v) params.delete(k);
  const items = await api("/api/items?" + params);
  const list = $("#items-list");
  const html = items.map(itemCard).join("");
  list.innerHTML = append ? list.innerHTML + html : (html || '<div class="empty">Brak zakładek. Dodaj źródło w zakładce <a href="#zrodla">Źródła</a>.</div>');
  $("#items-more").style.display = items.length === 30 ? "" : "none";
  state.itemsOffset += items.length;
}

$("#items-filter").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  state.itemsFilter = Object.fromEntries(new FormData(e.target));
  await loadItems();
}));
$("#items-more").addEventListener("click", guard(() => loadItems(true)));

async function openItem(id) {
  const it = await api(`/api/items/${id}`);
  const list = (title, arr) => arr && arr.length ? `<section><h3>${title}</h3><ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>` : "";
  $("#drawer-content").innerHTML = `
    <div class="item-top">${sourceBadge(it.source)}<span>${esc(it.context || "")}</span><span>${esc(it.author || "")}</span><span>${fmtDate(it.created_at)}</span>${stars(it.usefulness)}</div>
    <h2>${esc(it.title || "(bez tytułu)")}</h2>
    ${it.url ? `<p><a href="${esc(it.url)}" target="_blank" rel="noopener">Otwórz oryginał ↗</a></p>` : ""}
    ${it.summary ? `<section><h3>Streszczenie agenta</h3><p>${esc(it.summary)}</p><div class="chips">${chips(it.topics)}${chips(it.technologies, "tech")}</div></section>` : ""}
    ${list("Najważniejsze wnioski", it.key_insights)}
    ${list("Co można z tym zrobić", it.idea_seeds)}
    ${it.suggestions?.length ? `<section><h3>Propozycje powiązane z tą zakładką</h3><div class="sugg-list">${it.suggestions.map(suggCard).join("")}</div></section>` : ""}
    ${it.text ? `<section><h3>Treść</h3><div class="fulltext">${esc(it.text)}</div></section>` : ""}
    ${it.error ? `<p class="small" style="color:var(--err)">Błąd: ${esc(it.error)}</p>` : ""}
    <div class="row-end"><button class="danger" data-del-item="${it.id}">Usuń</button><button data-analyze="${it.id}">🔍 ${it.summary ? "Analizuj ponownie" : "Analizuj teraz"}</button><button class="primary" data-ask-item="${it.id}">🤖 Zapytaj agenta</button></div>`;
  $("#drawer").classList.remove("hidden");
}
const closeDrawer = () => $("#drawer").classList.add("hidden");
$("#drawer-close").addEventListener("click", closeDrawer);
$("#drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ------------------------------------------------------------------ POMYSŁY
async function loadSuggestions() {
  const kind = $("#sugg-kind").value;
  const params = new URLSearchParams({ status: state.suggStatus });
  if (kind) params.set("kind", kind);
  const rows = await api("/api/suggestions?" + params);
  $("#sugg-list").innerHTML = rows.length ? rows.map(suggCard).join("") : '<div class="empty">Nic tu jeszcze nie ma.</div>';
}
$("#sugg-tabs").addEventListener("click", guard(async (e) => {
  const b = e.target.closest("button[data-status]");
  if (!b) return;
  state.suggStatus = b.dataset.status;
  $$("#sugg-tabs button").forEach((x) => x.classList.toggle("active", x === b));
  await loadSuggestions();
}));
$("#sugg-kind").addEventListener("change", guard(loadSuggestions));
$("#ideas-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  await startJob("ideas", { focus: f.focus, count: +f.count });
}));

// ------------------------------------------------------------------ PROJEKTY
async function loadProjects() {
  const [projects, sugg] = await Promise.all([api("/api/projects"), api("/api/suggestions?kind=project_use")]);
  const counts = {};
  sugg.forEach((s) => { if (s.project_id && s.status !== "dismissed") counts[s.project_id] = (counts[s.project_id] || 0) + 1; });
  const statusLbl = { active: "aktywny", paused: "wstrzymany", done: "zakończony" };
  $("#projects-list").innerHTML = projects.length ? projects.map((p) => `
    <div class="project">
      <h4>${esc(p.name)}</h4><div class="sugg-meta">${statusLbl[p.status] || p.status} · ${counts[p.id] || 0} propozycji z zakładek</div>
      <dl>${p.description ? `<dt>Opis</dt><dd>${esc(p.description)}</dd>` : ""}${p.stack ? `<dt>Stack</dt><dd>${esc(p.stack)}</dd>` : ""}${p.goals ? `<dt>Cele</dt><dd>${esc(p.goals)}</dd>` : ""}</dl>
      <div class="row-end"><button class="small danger" data-del-project="${p.id}">Usuń</button><button class="small" data-edit-project='${esc(JSON.stringify(p))}'>Edytuj</button></div>
    </div>`).join("") : '<div class="empty">Dodaj pierwszy projekt — agent zacznie dopasowywać do niego zakładki.</div>';
}
$("#project-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const id = f.id; delete f.id;
  await api(id ? `/api/projects/${id}` : "/api/projects", { method: id ? "PUT" : "POST", body: f });
  e.target.reset(); e.target.id.value = "";
  toast("Zapisano projekt");
  await loadProjects();
}));
$("#project-reset").addEventListener("click", () => { $("#project-form").reset(); $("#project-form").id.value = ""; });

// ------------------------------------------------------------------ AGENT (czat)
async function loadConversations() {
  const convs = await api("/api/conversations");
  $("#conv-list").innerHTML = convs.map((c) => `<div class="conv ${c.id === state.convId ? "active" : ""}" data-conv="${c.id}"><span>${esc(c.title || "Rozmowa")}</span><button class="del" data-del-conv="${c.id}" title="Usuń">✕</button></div>`).join("")
    || '<p class="muted small">Brak rozmów.</p>';
  if (!state.convId && convs.length) await openConversation(convs[0].id);
  if (!convs.length) renderChat([]);
}

function renderMsg(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
  const trace = (m.tools || []).length ? `<div class="tool-trace">${m.tools.map((t) => `<span title="${esc(JSON.stringify(t.input))}">⚙ ${esc(t.tool)}</span>`).join("")}</div>` : "";
  return `<div class="msg assistant">${trace}<div class="md">${md(m.text || "")}</div></div>`;
}
function renderChat(messages) {
  const log = $("#chat-log");
  log.innerHTML = messages.length ? messages.map(renderMsg).join("")
    : `<div class="empty">Zapytaj o swoje zakładki, poproś o pomysły albo opowiedz agentowi o sobie — zapamięta to.</div>`;
  log.scrollTop = log.scrollHeight;
  $("#chat-hints").style.display = messages.length ? "none" : "";
}
async function openConversation(id) {
  state.convId = id;
  const conv = await api(`/api/conversations/${id}`);
  renderChat(conv.messages);
  $$(".conv").forEach((c) => c.classList.toggle("active", +c.dataset.conv === id));
}
async function sendChat(text) {
  if (state.sending || !text.trim()) return;
  state.sending = true;
  const btn = $("#chat-form button"); btn.disabled = true;
  try {
    if (!state.convId) { const c = await api("/api/conversations", { method: "POST" }); state.convId = c.id; }
    const log = $("#chat-log");
    if (log.querySelector(".empty")) log.innerHTML = "";
    $("#chat-hints").style.display = "none";
    log.insertAdjacentHTML("beforeend", renderMsg({ role: "user", text }) + '<div class="msg assistant typing" id="typing">Agent myśli i przegląda Twoją bazę…</div>');
    log.scrollTop = log.scrollHeight;
    const res = await api(`/api/conversations/${state.convId}/messages`, { method: "POST", body: { text } });
    $("#typing")?.remove();
    log.insertAdjacentHTML("beforeend", renderMsg({ role: "assistant", text: res.reply, tools: res.events }));
    log.scrollTop = log.scrollHeight;
    await loadConversations();
  } catch (e) {
    $("#typing")?.remove();
    toast(e.message, true);
  } finally {
    state.sending = false; btn.disabled = false;
  }
}
$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const t = e.target.text; const v = t.value; t.value = ""; sendChat(v); });
$("#chat-form textarea").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); } });
$("#chat-hints").addEventListener("click", (e) => { if (e.target.tagName === "BUTTON") sendChat(e.target.textContent); });
$("#new-chat").addEventListener("click", guard(async () => {
  const c = await api("/api/conversations", { method: "POST" });
  state.convId = c.id; renderChat([]); await loadConversations();
}));
$("#conv-list").addEventListener("click", guard(async (e) => {
  const del = e.target.closest("[data-del-conv]");
  if (del) {
    e.stopPropagation();
    if (!confirm("Usunąć tę rozmowę?")) return;
    await api(`/api/conversations/${del.dataset.delConv}`, { method: "DELETE" });
    if (+del.dataset.delConv === state.convId) state.convId = null;
    return loadConversations();
  }
  const c = e.target.closest("[data-conv]");
  if (c) await openConversation(+c.dataset.conv);
}));
async function askAgent(prompt) {
  closeDrawer();
  const c = await api("/api/conversations", { method: "POST" });
  state.convId = c.id;
  location.hash = "#agent";
  setTimeout(() => sendChat(prompt), 50);
}

// ------------------------------------------------------------------ PAMIĘĆ
async function loadMemory() {
  const rows = await api("/api/memory");
  const groups = {};
  rows.forEach((m) => (groups[m.category] ||= []).push(m));
  const originLbl = { agent: "zapisał agent", user: "dodane ręcznie", auto: "aktualizowane automatycznie" };
  $("#memory-list").innerHTML = Object.keys(groups).length ? Object.entries(groups).map(([cat, items]) => `
    <div class="mem-group"><h4>${esc(cat)}</h4>${items.map((m) => `
      <div class="mem"><p>${esc(m.content)}<br><span class="origin">${originLbl[m.origin] || m.origin} · ${fmtDate(m.updated_at)}</span></p>
      <button class="small" data-edit-mem="${m.id}">Edytuj</button><button class="small danger" data-del-mem="${m.id}">Usuń</button></div>`).join("")}</div>`).join("")
    : '<div class="empty">Pamięć jest pusta. Opowiedz agentowi o swoich umiejętnościach i celach albo dodaj wpis powyżej.</div>';
}
$("#memory-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  await api("/api/memory", { method: "POST", body: { category: f.category || "ogólne", content: f.content } });
  e.target.reset(); await loadMemory();
}));

// ------------------------------------------------------------------ ŹRÓDŁA
async function loadSources() {
  const s = await api("/api/sources");
  const last = (iso) => iso ? `ostatnia synchronizacja: ${new Date(iso).toLocaleString("pl-PL")}` : "jeszcze nie synchronizowano";
  $("#src-reddit").innerHTML = `
    <h3>🟠 Reddit <span class="state ${s.reddit.configured ? "on" : ""}">${s.reddit.configured ? "API skonfigurowane" : "API nieskonfigurowane"}</span></h3>
    <p class="muted small">${s.reddit.configured ? `u/${esc(s.reddit.username)} · ${last(s.reddit.last_sync)}` : "Reddit wymaga zatwierdzenia dostępu do Data API dla nowych aplikacji. Do tego czasu użyj bookmarkletu."}</p>
    ${s.reddit.configured ? '<button class="primary" data-job="sync" data-source="reddit">⟳ Synchronizuj Reddit</button>' : ""}
    <details ${s.reddit.configured ? "" : "open"}><summary>Bez API: bookmarklet (eksport zapisanych w przeglądarce)</summary>
      <ol><li>Przeciągnij ten przycisk na pasek zakładek: <a class="bm" href="${esc(s.reddit_bookmarklet)}">Inspiro: eksport Reddit</a></li>
      <li>Otwórz reddit.com (zalogowany) i kliknij zakładkę — pobierze się <code>reddit-saved.json</code>.</li>
      <li>Zaimportuj plik poniżej. Duplikaty są pomijane, więc możesz to powtarzać.</li></ol></details>
    <details><summary>Konfiguracja API</summary><ol><li>Złóż wniosek o dostęp do Reddit Data API i utwórz aplikację typu <b>script</b> na reddit.com/prefs/apps.</li><li>Wpisz <code>REDDIT_CLIENT_ID</code>, <code>REDDIT_CLIENT_SECRET</code>, <code>REDDIT_USERNAME</code>, <code>REDDIT_PASSWORD</code> do <code>.env</code> i zrestartuj aplikację.</li></ol></details>`;
  const xState = s.x.connected ? "połączono" : s.x.configured ? "gotowe do połączenia" : "nieskonfigurowane";
  $("#src-x").innerHTML = `
    <h3>✖ X <span class="state ${s.x.connected ? "on" : ""}">${xState}</span></h3>
    <p class="muted small">${s.x.connected ? last(s.x.last_sync) : "Zakładki przez oficjalne API (pay-per-use; odczyt własnych zakładek to tani „owned read”)."}</p>
    <div class="actions">${s.x.connected ? '<button class="primary" data-job="sync" data-source="x">⟳ Synchronizuj X</button><button class="ghost" id="x-disconnect">Rozłącz</button>'
      : s.x.configured ? '<a class="btn primary" href="/auth/x/login" style="background:var(--accent);color:#fff;border-color:var(--accent)">Połącz z X</a>' : ""}</div>
    <details ${s.x.configured ? "" : "open"}><summary>Konfiguracja API</summary><ol>
      <li>W konsoli deweloperskiej X utwórz aplikację, włącz OAuth 2.0 (typ: Native App / public client) i doładuj kredyty.</li>
      <li>Callback URL: <code>${esc(s.x_redirect_uri)}</code></li>
      <li>Wpisz <code>X_CLIENT_ID</code> (i ewentualnie <code>X_CLIENT_SECRET</code>) do <code>.env</code>, zrestartuj i kliknij „Połącz z X”.</li></ol></details>
    <details><summary>Bez API: skrypt do konsoli przeglądarki (eksperymentalny)</summary>
      <ol><li>Otwórz <a href="https://x.com/i/bookmarks" target="_blank" rel="noopener">x.com/i/bookmarks</a>, naciśnij F12 → Console.</li>
      <li>Wklej skrypt (<button class="small" id="copy-x">Kopiuj</button>) i poczekaj — strona sama się przewija i pobiera <code>x-bookmarks.json</code>.</li>
      <li>Zaimportuj plik poniżej.</li></ol></details>`;
  $("#copy-x")?.addEventListener("click", () => navigator.clipboard.writeText(s.x_scraper).then(() => toast("Skopiowano skrypt")));
  $("#x-disconnect")?.addEventListener("click", guard(async () => { await api("/api/sources/x/disconnect", { method: "POST" }); loadSources(); }));
}
$("#import-file").addEventListener("change", guard(async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const content = await file.text();
  const res = await api("/api/import", { method: "POST", body: { filename: file.name, content } });
  $("#import-result").innerHTML = `✓ Znaleziono ${res.parsed}, dodano <b>${res.added}</b> nowych, pominięto ${res.skipped} znanych. <button class="small primary" data-job="analyze">🔍 Analizuj teraz</button>`;
  e.target.value = "";
}));
$("#manual-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const res = await api("/api/items", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
  e.target.reset();
  toast(res.created ? "Dodano zakładkę — uruchom analizę" : "Ta zakładka już jest w bazie");
}));

// ------------------------------------------------------------------ zadania w tle
async function startJob(kind, params = {}) {
  const job = await api("/api/jobs", { method: "POST", body: { kind, params } });
  toast(`${JOB_LABELS[kind] || kind}: start`);
  pollJobs();
  return job;
}
const seenFinished = new Set();
async function pollJobs() {
  clearTimeout(pollJobs._t);
  let jobs = [];
  try { jobs = await api("/api/jobs"); } catch { return; }
  const recent = jobs.filter((j) => j.status === "running" || !j.finished_at || Date.now() - new Date(j.finished_at) < 60000);
  $("#jobs").innerHTML = recent.map((j) => {
    const pct = j.total ? Math.round((100 * j.done) / j.total) : (j.status === "running" ? 15 : 100);
    const lastLog = j.error || (j.log || []).slice(-1)[0] || "";
    const icon = j.status === "running" ? "⏳" : j.status === "done" ? "✓" : "⚠";
    return `<div class="job ${j.status}"><b>${icon} ${JOB_LABELS[j.kind] || j.kind}</b>${j.total ? ` · ${j.done}/${j.total}` : ""}<div class="small">${esc(lastLog).slice(0, 120)}</div><div class="bar"><i style="width:${pct}%"></i></div></div>`;
  }).join("");
  for (const j of jobs) {
    if (j.status !== "running" && !seenFinished.has(j.id)) {
      seenFinished.add(j.id);
      if (pollJobs._initialized) {
        if (j.status === "error") toast(`${JOB_LABELS[j.kind]}: ${j.error}`, true);
        else toast(`${JOB_LABELS[j.kind]}: gotowe`);
        route();
      }
    }
  }
  pollJobs._initialized = true;
  if (jobs.some((j) => j.status === "running")) pollJobs._t = setTimeout(pollJobs, 1500);
}

// ------------------------------------------------------------------ delegacja kliknięć
document.addEventListener("click", guard(async (e) => {
  const t = e.target;
  const el = (sel) => t.closest(sel);
  let x;
  if ((x = el("[data-job]"))) { e.preventDefault(); return startJob(x.dataset.job, x.dataset.source ? { source: x.dataset.source } : {}); }
  if ((x = el(".itemref"))) { e.preventDefault(); return openItem(x.dataset.item); }
  if ((x = el("[data-search]"))) {
    e.stopPropagation();
    state.itemsFilter = { q: x.dataset.search };
    $("#items-filter").q.value = x.dataset.search;
    closeDrawer();
    if (location.hash !== "#zakladki") location.hash = "#zakladki"; else loadItems();
    return;
  }
  if ((x = el("[data-sugg]"))) {
    await api(`/api/suggestions/${x.dataset.sugg}`, { method: "PATCH", body: { status: x.dataset.status } });
    toast("Zapisano"); return loaders[location.hash.slice(1).split("?")[0] || "pulpit"]?.();
  }
  if ((x = el("[data-ask]"))) {
    const card = x.closest(".sugg");
    return askAgent(`Rozwiń ten pomysł w konkretny plan (zakres MVP, technologie, pierwsze zadania, ryzyka). Odwołaj się do zakładek, na których bazuje.\n\nPomysł: ${card.querySelector(".sugg-title").textContent}\n${card.querySelector(".sugg-body").textContent}`);
  }
  if ((x = el("[data-ask-item]"))) return askAgent(`Jak mogę wykorzystać zakładkę [#${x.dataset.askItem}] w moich projektach albo w czymś nowym? Najpierw ją przeczytaj.`);
  if ((x = el("[data-analyze]"))) {
    x.disabled = true; x.textContent = "Analizuję…";
    await api(`/api/items/${x.dataset.analyze}/analyze`, { method: "POST" });
    toast("Przeanalizowano"); return openItem(x.dataset.analyze);
  }
  if ((x = el("[data-del-item]"))) {
    if (!confirm("Usunąć zakładkę z bazy Inspiro? (Oryginał zostaje nietknięty)")) return;
    await api(`/api/items/${x.dataset.delItem}`, { method: "DELETE" }); closeDrawer(); return loadItems();
  }
  if ((x = el("[data-edit-project]"))) {
    const p = JSON.parse(x.dataset.editProject), f = $("#project-form");
    for (const k of ["id", "name", "description", "stack", "goals", "status"]) f[k].value = p[k] ?? "";
    f.scrollIntoView({ behavior: "smooth" }); return;
  }
  if ((x = el("[data-del-project]"))) {
    if (!confirm("Usunąć projekt?")) return;
    await api(`/api/projects/${x.dataset.delProject}`, { method: "DELETE" }); return loadProjects();
  }
  if ((x = el("[data-del-mem]"))) { await api(`/api/memory/${x.dataset.delMem}`, { method: "DELETE" }); return loadMemory(); }
  if ((x = el("[data-edit-mem]"))) {
    const p = x.closest(".mem").querySelector("p").firstChild.textContent;
    const v = prompt("Treść wpisu:", p);
    if (v && v.trim()) { await api(`/api/memory/${x.dataset.editMem}`, { method: "PUT", body: { content: v } }); loadMemory(); }
    return;
  }
  if ((x = el(".item[data-id]"))) return openItem(x.dataset.id);
}));

const loaders = {
  pulpit: guard(loadDashboard),
  zakladki: guard(() => loadItems()),
  pomysly: guard(loadSuggestions),
  projekty: guard(loadProjects),
  agent: guard(loadConversations),
  pamiec: guard(loadMemory),
  zrodla: guard(loadSources),
};

route();
pollJobs();
