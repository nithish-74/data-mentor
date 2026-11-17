"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (s) => DOMPurify.sanitize(marked.parse(String(s || "")));
const store = { get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }, del(k) { try { localStorage.removeItem(k); } catch (_) {} } };
let ME = null, CFG = null;

// ---------------------------------------------------------------- helpers
async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: { "X-Mentor": "1" } };
  if (opts.body) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  let r;
  try { r = await fetch(path, init); }
  catch (_) { throw new Error("Can't reach the mentor right now. Check your internet, or try again in a minute."); }
  if (r.headers.get("ngrok-error-code") || r.status === 502 || r.status === 503 || r.status === 504 ||
      (r.status === 404 && !(r.headers.get("content-type") || "").includes("json"))) {
    throw new Error("Mentor is reconnecting. Try again in a minute; your progress is safe.");
  }
  let data = {};
  try { data = await r.json(); } catch (_) { /* no body */ }
  if (r.status === 401 && !path.startsWith("/api/login")) { showSignin(); throw new Error("Please sign in again."); }
  const msg = data.error || (typeof data.detail === "string" ? data.detail : "");
  if (!r.ok) throw new Error(msg || `Something went wrong (${r.status}). Please try again.`);
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3800);
}
function busy(el, text) {
  el.innerHTML = `<div class="busy"><span class="dots"><i></i><i></i><i></i></span>${esc(text)}</div>`;
}
async function withButton(btn, fn) {
  if (btn) btn.disabled = true;
  try { await fn(); } catch (e) { toast(e.message); } finally { if (btn) btn.disabled = false; }
}
/** Inline "are you sure" next to a button: no browser dialogs. */
function confirmInline(btn, question, yesLabel, onYes) {
  const box = document.createElement("span");
  box.className = "confirm";
  box.innerHTML = `<span>${esc(question)}</span><button class="btn sm danger" type="button">${esc(yesLabel)}</button><button class="btn sm quiet" type="button">Cancel</button>`;
  btn.hidden = true; btn.after(box);
  const [yes, no] = $$("button", box);
  no.onclick = () => { box.remove(); btn.hidden = false; };
  yes.onclick = () => withButton(yes, async () => { await onYes(); box.remove(); btn.hidden = false; });
}
const greeting = () => { const h = new Date().getHours(); return h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
const firstName = () => (ME.name || "").split(/[\s._-]/)[0].replace(/^./, (c) => c.toUpperCase());
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const lessonTitle = (week, day, topic) => `Week ${week}, lesson ${day}: ${topic}`;
const fmtDate = (iso) => new Date(iso + "T00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });

// ---------------------------------------------------------------- sign in
const params = new URLSearchParams(location.search);
if (params.get("invite")) sessionStorage.setItem("invite", params.get("invite").toUpperCase());
const invite = () => ($("#invite-code").value.trim() || sessionStorage.getItem("invite") || "").toUpperCase();

function showSignin() {
  $("#shell").hidden = true; $("#signin").hidden = false;
  $("#invite-note").hidden = !sessionStorage.getItem("invite");
  const last = store.get("username");
  if (last && !$("#f-username").value) $("#f-username").value = last;
  $("#signin-app").innerHTML = installCard("signin");
  wireInstall();
  ($("#f-username").value ? $("#signin-form button[type=submit]") : $("#f-username")).focus();
}
$("#signin-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const err = $("#signin-error"); err.hidden = true;
  const username = $("#f-username").value.trim().toLowerCase();
  if (username.length < 3) { err.textContent = "Usernames are at least 3 characters."; err.hidden = false; return; }
  withButton(e.submitter, async () => {
    try {
      await api("/api/login", { body: { username, password: $("#f-password").value, invite: invite() } });
      store.set("username", username);
      sessionStorage.removeItem("invite");
      $("#f-password").value = "";
      history.replaceState(null, "", "/#home");
      await boot();
    } catch (ex) {
      if (ex.message === "need_password") { $("#pw-field").hidden = false; $("#f-password").focus(); return; }
      if (ex.message === "wrong_password") { $("#pw-field").hidden = false; err.textContent = "That password isn't right."; }
      else if (ex.message === "need_invite") { $("#need-invite").hidden = false; $("#invite-code").focus(); return; }
      else err.textContent = ex.message;
      err.hidden = false;
    }
  });
});

// ---------------------------------------------------------------- routing
const views = { home, learn, progress, tests, articles, ask, people, profile };
async function route() {
  const [name, ...args] = (location.hash.slice(1) || "home").split("/");
  const fn = views[name] || home;
  $$(".rail a.nav").forEach((a) => a.classList.toggle("active", a.dataset.view === (views[name] ? name : "home")));
  window.scrollTo(0, 0);
  try { await fn(args); } catch (e) { view.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}
window.addEventListener("hashchange", route);
async function refreshMe() {
  ME = await api("/api/me");
  $("#me-box").innerHTML = `<b>${esc(ME.name)}</b>@${esc(ME.username || "")}`;
  // inside the Android app: hand the reminder times to the phone so it can set its alarms
  if (window.MentorApp && ME.schedule) { try { window.MentorApp.setSchedule(JSON.stringify(ME.schedule)); } catch (_) {} }
  const badge = $("#art-badge");
  badge.hidden = !ME.articles_new; badge.textContent = ME.articles_new > 99 ? "99+" : ME.articles_new;
  return ME;
}
// ---------------------------------------------------------------- installable app + notifications
const APP = {
  prompt: null,
  standalone: () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true,
  ios: () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
  pushSupported: () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && isSecureContext,
};
if ("serviceWorker" in navigator && isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault(); APP.prompt = e;
  $$("[data-install]").forEach((b) => (b.hidden = false));
  if (!APP.android()) $$("[data-install-hint]").forEach((h) => (h.hidden = true));
});
window.addEventListener("appinstalled", () => { APP.prompt = null; toast("Installed. Look for Mentor on your home screen."); });

APP.android = () => /android/i.test(navigator.userAgent);
APP.edge = () => /edg\//i.test(navigator.userAgent);
/** What "get the app" means on this device. Each route ends with Mentor in the device's own app list. */
function installSteps() {
  if (APP.android()) return {
    lead: "Install Mentor on this phone. It appears with your other apps and sends your daily reminder.",
    actions: `<a class="btn primary" href="/download/android" download>Download for Android</a>`,
    hint: "Open the downloaded file and tap Install. The first time, Android asks you to allow installs from this browser: allow it, then come back and tap Install." };
  if (APP.ios()) return {
    lead: "Add Mentor to your Home Screen. It opens like an app and can send your daily reminder.",
    actions: "",
    hint: "In Safari, tap the Share button (the square with an arrow), then Add to Home Screen, then Add." };
  return {
    lead: "Install Mentor on this computer. It gets its own window, appears in your Start menu and app list next to your other apps, and sends your daily reminder.",
    actions: `<button class="btn primary" data-install ${APP.prompt ? "" : "hidden"}>Install the app</button>`,
    hint: APP.prompt ? "" : APP.edge()
      ? "In Edge: click the app icon at the right end of the address bar (it says App available), or open the ⋯ menu → Apps → Install this site as an app."
      : "In Chrome: click the install icon at the right end of the address bar, or open the ⋮ menu → Cast, save and share → Install page as app." };
}
function installCard(where) {
  if (window.MentorApp || APP.standalone() || store.get("install-dismissed") === "1" && where === "home") return "";
  const s = installSteps();
  return `<section class="panel install-card"><div class="panel-head"><h2>Get the app</h2>
      ${where === "home" ? `<button class="btn sm quiet" data-dismiss-install>Not now</button>` : ""}</div>
    <p class="muted">${s.lead}</p>
    ${s.actions ? `<div class="row">${s.actions}</div>` : ""}
    ${s.hint ? `<p class="hint" data-install-hint>${s.hint}</p>` : ""}</section>`;
}
function wireInstall() {
  $$("[data-install]").forEach((b) => (b.onclick = async () => {
    if (!APP.prompt) return;
    APP.prompt.prompt();
    const r = await APP.prompt.userChoice.catch(() => ({}));
    if (r.outcome === "accepted") APP.prompt = null;
  }));
  $$("[data-dismiss-install]").forEach((b) => (b.onclick = () => { store.set("install-dismissed", "1"); b.closest(".install-card").remove(); }));
}

function b64ToBytes(b64) {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
/** The service worker registration, or null if this browser won't run one (never hangs). */
async function swReady(ms = 6000) {
  if (!("serviceWorker" in navigator)) return null;
  try {
    const reg = (await navigator.serviceWorker.getRegistration()) || (await navigator.serviceWorker.register("/sw.js"));
    if (reg.active) return reg;
    return await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), ms))]);
  } catch (_) { return null; }
}
async function currentSubscription() {
  if (!APP.pushSupported()) return null;
  const reg = await swReady(2500);
  return reg ? reg.pushManager.getSubscription() : null;
}
async function enablePush() {
  if (!APP.pushSupported()) throw new Error(APP.ios() && !APP.standalone()
    ? "On iPhone, add the app to your Home Screen first, open it from there, then turn notifications on."
    : "This browser can't show notifications from websites.");
  const reg = await swReady();
  if (!reg) throw new Error("This browser won't run the app's background helper, so it can't show notifications. Try Chrome, Edge, Firefox or Safari.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Notifications are blocked for this site. Allow them in your browser's site settings, then try again.");
  const { key } = await api("/api/push/key");
  const sub = (await reg.pushManager.getSubscription()) ||
    await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
  await api("/api/push/subscribe", { body: { subscription: sub.toJSON() } });
  await api("/api/push/test", { body: {} });
}
async function disablePush() {
  const sub = await currentSubscription();
  if (sub) { await api("/api/push/unsubscribe", { body: { endpoint: sub.endpoint } }); await sub.unsubscribe(); }
}

async function boot() {
  CFG = CFG || await (await fetch("/api/config")).json();
  try { await refreshMe(); } catch (_) { return; }
  $("#signin").hidden = true; $("#shell").hidden = false;
  $$(".admin-only").forEach((a) => (a.hidden = ME.role !== "admin"));
  route();
}

// ---------------------------------------------------------------- shared: open task + review
const KIND_LABEL = { lesson: "Assignment", initial: "Assessment", assessment: "Weekly written test", challenge: "Architecture challenge", free: "Task" };
function taskPanel(c) {
  return `<section class="panel" id="task">
    <div class="panel-head"><div class="stack" style="gap:4px"><span class="label">${esc(KIND_LABEL[c.kind] || "Task")} · waiting for you</span>
      <h2>${esc(c.topic)}</h2></div></div>
    <div class="prose">${md(c.assignment)}</div>
    <label class="field" for="answer"><span>Your answer</span>
      <textarea id="answer" placeholder="Write it the way you'd explain it to a colleague: the design, the SQL, and why. Saying what you're unsure about is fine."></textarea></label>
    <div class="row"><button class="btn primary" id="submit">Send for review</button><button class="btn quiet" id="skip">Skip this one</button>
      <span class="hint" id="draft-note"></span></div>
    <div id="review"></div>
  </section>`;
}
function wireTask() {
  const btn = $("#submit"); if (!btn) return;
  const key = "draft:" + (ME.progress.current?.topic || "");
  const ta = $("#answer");
  ta.value = store.get(key) || "";
  if (ta.value) $("#draft-note").textContent = "Draft restored.";
  ta.addEventListener("input", () => { store.set(key, ta.value); $("#draft-note").textContent = "Draft saved on this device."; });
  btn.onclick = () => withButton(btn, async () => {
    const answer = ta.value.trim();
    if (answer.length < 3) { toast("Write your answer first."); ta.focus(); return; }
    busy($("#review"), "Your mentor is reading your answer. This usually takes 10 to 30 seconds.");
    try {
      const r = await api("/api/submit", { body: { answer } });
      store.del(key);
      $("#review").innerHTML = reviewHtml(r);
      btn.hidden = $("#skip").hidden = true; ta.readOnly = true;
      await refreshMe();
    } catch (e) { $("#review").innerHTML = ""; throw e; }
  });
  $("#skip").onclick = () => confirmInline($("#skip"), "Skip it? It won't be scored or ticked.", "Skip", async () => {
    await api("/api/skip", { body: {} }); await refreshMe(); route();
  });
}
function reviewHtml(r) {
  const tick = r.ticked?.length
    ? `<p class="note good">Ticked on your progress list: <strong>${esc(r.ticked[0])}</strong>. <a href="#progress">See progress</a></p>`
    : r.kind === "lesson" && r.score != null
      ? `<p class="note warn">Not ticked yet. You need ${r.pass_mark}/10 for that. Have another read, try a quick test, or tick it yourself on the <a href="#progress">progress page</a> once you're confident.</p>` : "";
  return `<div class="stack" style="margin-top:8px;padding-top:18px;border-top:1px solid var(--rule)">
    <div class="row" style="justify-content:space-between"><span class="label">Mentor's review</span>
      ${r.score != null ? `<span><span class="score-big">${r.score}</span><span class="muted"> / 10</span></span>` : ""}</div>
    ${tick}
    <div class="prose">${md(r.feedback)}</div>
    ${r.weak_areas?.length ? `<p class="hint">Noted to revisit: ${r.weak_areas.map(esc).join(", ")}</p>` : ""}
    <div class="row"><a class="btn primary" href="#learn/new">Next lesson</a><a class="btn" href="#tests">Quick test</a></div></div>`;
}

// ---------------------------------------------------------------- Today
function studyLog(p) {
  const days = new Set(p.activity), today = new Date(p.today + "T00:00");
  // 4 rows of Mon..Sun, the last row is the current week
  const monday = new Date(today); monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) - 21);
  let cells = "", thisWeek = 0;
  for (let i = 0; i < 28; i++) {
    const d = new Date(monday); d.setDate(monday.getDate() + i);
    const local = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const on = days.has(local), isToday = local === p.today, future = d > today;
    if (on && i >= 21) thisWeek++;
    cells += `<i class="${on ? "on" : ""} ${isToday ? "today" : ""}" title="${esc(fmtDate(local))}${on ? ": studied" : ""}" style="${future ? "opacity:.35" : ""}"></i>`;
  }
  return { html: `<div class="log" aria-label="Study log, last four weeks">${cells}</div>`, thisWeek };
}
async function home() {
  const p = ME.progress;
  const all = p.modules.flatMap((m) => m.lessons.map((l, i) => ({ ...l, week: m.week, day: i + 1, module: m.title })));
  const next = all.find((l) => !l.done);
  const week = p.modules[(next ? next.week : 12) - 1];
  const log = studyLog(p);
  const c = p.current;
  const todayPanel = c ? `
      <section class="panel today">
        <div class="kicker"><span class="hl">Today</span><span class="label">${esc(KIND_LABEL[c.kind] || "Task")}</span></div>
        <h2>${esc(c.topic)}</h2>
        <p class="muted">You have an answer to finish. Send it and your mentor will review it${c.kind === "lesson" ? " and tick the topic if you pass" : ""}.</p>
        <div class="row">${c.kind === "lesson" ? `<a class="btn primary" href="#learn/${p.lesson_index - 1}">Continue the assignment</a>` : `<a class="btn primary" href="#tests">Continue the test</a>`}</div>
      </section>`
    : next ? `
      <section class="panel today">
        <div class="kicker"><span class="hl">Today</span><span class="label">Week ${next.week} · lesson ${next.day} of 5</span></div>
        <h2>${esc(next.topic)}</h2>
        <p class="muted">${p.lesson_index ? "About 20 minutes of reading, then one small design task." : "Your first lesson. About 20 minutes of reading, then one small design task to try."}</p>
        <div class="row"><a class="btn primary" href="#learn/new">Start the lesson</a></div>
      </section>`
    : `<section class="panel today"><h2>You've finished all 60 lessons.</h2><p class="muted">Keep sharp with week tests and questions to your mentor.</p></section>`;

  view.innerHTML = `
    <header class="page-head">
      <span class="label">${new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</span>
      <h1>${greeting()}, ${esc(firstName())}.</h1>
      <div class="facts-line">
        <span><b>${p.checked}</b> of ${p.total} topics done</span>
        <span>${p.streak ? `<b>${plural(p.streak, "day")}</b> in a row` : "No streak yet"}</span>
        ${p.average != null ? `<span>average score <b>${p.average}</b></span>` : ""}
      </div>
    </header>
    <div class="split">
      <div class="stack" style="gap:20px">${todayPanel}
        ${p.weak_areas.length ? `<section class="panel"><div class="panel-head"><h2>Worth another look</h2><button class="btn sm" id="revise">Revise these</button></div>
          <div class="row">${p.weak_areas.map((w) => `<span class="chip warn">${esc(w)}</span>`).join("")}</div><div id="revise-out"></div></section>` : ""}
      </div>
      <aside class="stack" style="gap:20px">
        <section class="panel">
          <span class="label">This week</span>
          <h3>Week ${week.week}: ${esc(week.title)}</h3>
          <div class="mini-list">${week.lessons.map((l) => `<div><span class="tick ${l.checked ? "on" : ""}" aria-label="${l.checked ? "done" : "not done"}"></span><span class="${l.checked ? "muted" : ""}">${esc(l.topic)}</span></div>`).join("")}</div>
          <a href="#progress" class="hint">All 12 weeks</a>
        </section>
        ${installCard("home")}
        <section class="panel">
          <div class="panel-head"><span class="label">Study log</span><span class="hint">${log.thisWeek ? `${plural(log.thisWeek, "day")} this week` : "Nothing yet this week"}</span></div>
          ${log.html}
          <p class="hint">A square fills on days you submit work or tick a topic.</p>
        </section>
      </aside>
    </div>`;
  wireInstall();
  const rv = $("#revise");
  rv && (rv.onclick = () => withButton(rv, async () => {
    busy($("#revise-out"), "Putting together a short revision session…");
    const r = await api("/api/revision", { body: {} });
    $("#revise-out").innerHTML = `<div class="prose">${md(r.text)}</div>`;
  }));
}

// ---------------------------------------------------------------- Lessons
async function learn(args) {
  const p = ME.progress;
  const area = document.createElement("div");
  view.innerHTML = `<header class="page-head"><span class="label">Lessons</span><h1>Read, then try it.</h1>
    <p class="muted">Monday to Friday there's one lesson a day. Each ends with a short task your mentor reviews.</p></header>`;
  view.appendChild(area);
  const list = document.createElement("section");
  list.className = "panel";
  list.innerHTML = `<div class="panel-head"><h2>All lessons</h2><span class="hint">${p.lesson_index} of ${p.total} opened</span></div>
    <div class="table-wrap"><table class="table"><tbody>${p.modules.map((m) => m.lessons.map((l, i) => `
      <tr><td class="mono hint" style="width:4.5rem">W${String(m.week).padStart(2, "0")}·${i + 1}</td>
      <td>${l.done ? `<a href="#learn/${l.index}">${esc(l.topic)}</a>` : `<span class="muted">${esc(l.topic)}</span>`}</td>
      <td style="width:1%;white-space:nowrap">${l.checked ? `<span class="chip good">done</span>` : l.done ? `<span class="chip">opened</span>` : ""}</td></tr>`).join("")).join("")}
    </tbody></table></div>`;

  if (args[0] === "new") {
    if (p.current?.kind === "lesson") return showLesson(area, p.lesson_index - 1, true, list);
    busy(area, "Your mentor is writing today's lesson. This usually takes 10 to 30 seconds.");
    try {
      const r = await api("/api/lesson", { body: {} });
      await refreshMe();
      if (r.done) { area.innerHTML = `<section class="panel"><h2>That was the last lesson.</h2></section>`; return; }
      history.replaceState(null, "", `#learn/${r.index}`);
      return showLesson(area, r.index, true, list);
    } catch (e) { area.innerHTML = `<section class="panel"><p class="error">${esc(e.message)}</p></section>`; return; }
  }
  if (args[0] !== undefined) {
    const i = Number(args[0]);
    return showLesson(area, i, ME.progress.current?.kind === "lesson" && i === ME.progress.lesson_index - 1, list);
  }
  if (p.current?.kind === "lesson") return showLesson(area, p.lesson_index - 1, true, list);
  area.innerHTML = p.lesson_index < p.total
    ? `<section class="panel today"><div class="kicker"><span class="hl">Up next</span></div><h2>${esc(p.modules.flatMap((m) => m.lessons).find((l) => !l.done).topic)}</h2>
       <div class="row"><a class="btn primary" href="#learn/new">Start the lesson</a></div></section>` : "";
  view.appendChild(list);
}
async function showLesson(area, index, withTask, list) {
  const r = await api(`/api/lessons/${index}`);
  const m = r.title.match(/Week (\d+) · Lesson (\d)\/5 · (.*)/);
  area.innerHTML = `<article class="panel" style="padding:28px max(20px,3vw)">
      <div class="lesson-meta"><span class="label">${m ? `Week ${m[1]} · lesson ${m[2]} of 5` : "Lesson"}</span></div>
      <h2 style="font-size:clamp(1.35rem,1.1rem + 1vw,1.8rem);max-width:28ch">${esc(m ? m[3] : r.title)}</h2>
      <div class="prose">${md(r.text)}</div></article>
    ${withTask && ME.progress.current ? taskPanel(ME.progress.current) : ""}`;
  wireTask();
  if (list && !withTask) view.appendChild(list);
}

// ---------------------------------------------------------------- Progress (checklist as entity tables)
async function progress() {
  const p = ME.progress;
  const byMentor = p.modules.flatMap((m) => m.lessons).filter((l) => l.checked?.by === "mentor").length;
  const currentWeek = (p.modules.find((m) => m.lessons.some((l) => !l.checked)) || p.modules[11]).week;
  view.innerHTML = `
    <header class="page-head"><span class="label">Progress</span><h1>${p.checked} of ${p.total} topics done.</h1>
      <p class="muted" style="max-width:68ch">Your mentor ticks a topic when you pass its assignment (${p.pass_assignment}/10 or more)
      or that week's test (${p.pass_week}/10 or more). You can tick topics yourself too, for things you already know well.</p></header>
    <section class="panel">
      <div class="course-bar" aria-hidden="true">${p.modules.map((m) => `<i title="Week ${m.week}"><b style="width:${(m.lessons.filter((l) => l.checked).length / 5) * 100}%"></b></i>`).join("")}</div>
      <div class="course-legend" aria-hidden="true">${p.modules.map((m) => `<span>${m.week}</span>`).join("")}</div>
      <div class="facts-line"><span><b>${byMentor}</b> ticked by your mentor</span><span><b>${p.checked - byMentor}</b> ticked by you</span><span><b>${p.total - p.checked}</b> to go</span></div>
    </section>
    <div class="entities">${p.modules.map((m) => {
      const done = m.lessons.filter((l) => l.checked).length;
      return `<section class="entity ${m.week === currentWeek ? "current" : ""}">
        <div class="entity-head"><div><div class="code">week_${String(m.week).padStart(2, "0")}${m.challenge ? " · challenge" : ""}</div><h3>${esc(m.title)}</h3></div>
          <span class="chip ${done === 5 ? "good" : ""}">${done}/5</span></div>
        ${m.lessons.map((l) => {
          const c = l.checked;
          const who = c ? (c.by === "mentor" ? `<span class="who mentor" title="${esc(c.note || "")}">mentor · ${esc((c.note || "").replace("assignment ", "").replace("week test ", "test ") || fmtDate(c.date))}</span>` : `<span class="who">you · ${esc(fmtDate(c.date))}</span>`) : `<span class="who"></span>`;
          return `<label class="entity-row ${c ? "on" : ""}" for="chk-${l.index}"><input type="checkbox" id="chk-${l.index}" data-i="${l.index}" ${c ? "checked" : ""}>
            <span class="t">${esc(l.topic)}${l.done ? `<a class="open" href="#learn/${l.index}">read</a>` : ""}</span>${who}</label>`;
        }).join("")}</section>`;
    }).join("")}</div>`;
  $$("input[data-i]").forEach((box) => (box.onchange = async () => {
    box.disabled = true;
    try {
      await api("/api/check", { body: { index: Number(box.dataset.i), checked: box.checked } });
      const y = window.scrollY; await refreshMe(); await progress(); window.scrollTo(0, y);
      toast(box.checked ? "Ticked." : "Unticked.");
    } catch (e) { box.checked = !box.checked; box.disabled = false; toast(e.message); }
  }));
}

// ---------------------------------------------------------------- Tests
async function tests() {
  const p = ME.progress, recent = [...p.scores].reverse().slice(0, 8), none = !p.lesson_index;
  view.innerHTML = `
    <header class="page-head"><span class="label">Tests</span><h1>Check what stuck.</h1>
      <p class="muted">Multiple-choice tests are marked straight away. Written tests and challenges are read and scored by your mentor.</p></header>
    <div id="mcq-area"></div>
    <section class="panel"><div class="test-list">
      <div class="test-item"><div><h3>Quick check</h3><p class="muted">Five scenario questions on your last few lessons. About 5 minutes.</p></div><button class="btn primary" data-mcq="recent" data-n="5">Start</button></div>
      <div class="test-item"><div><h3>Week test</h3><p class="muted">Ten questions on this week's module. Score ${p.pass_week}/10 or more and your mentor ticks the week's topics.</p></div><button class="btn" data-mcq="week" data-n="10" ${none ? "disabled title='Open your first lesson first'" : ""}>Start</button></div>
      <div class="test-item"><div><h3>Written test</h3><p class="muted">Five open questions, including design and SQL. In weeks 4, 8 and 12 it's a full architecture challenge.</p></div><button class="btn" id="weekly" ${none ? "disabled title='Open your first lesson first'" : ""}>Get questions</button></div>
    </div></section>
    <div id="written"></div>
    <section class="panel"><h2>Recent scores</h2>${recent.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Date</th><th>What</th><th style="text-align:right">Score</th></tr></thead><tbody>
      ${recent.map((s) => `<tr><td class="hint num" style="white-space:nowrap">${esc(fmtDate(s.date))}</td><td>${esc(s.topic)}</td><td style="text-align:right"><span class="chip ${s.score >= 7 ? "good" : s.score >= 5 ? "" : "warn"} num">${s.score}/10</span></td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">No scores yet. Your first one will show up here.</p>`}</section>`;
  if (ME.mcq) renderMcq(ME.mcq);
  if (p.current && p.current.kind !== "lesson") { $("#written").innerHTML = taskPanel(p.current); wireTask(); }
  $$("[data-mcq]").forEach((b) => (b.onclick = () => withButton(b, async () => {
    busy($("#mcq-area"), "Writing your questions. This takes about 10 seconds.");
    $("#mcq-area").scrollIntoView({ behavior: "smooth", block: "start" });
    try { renderMcq(await api("/api/mcq", { body: { scope: b.dataset.mcq, n: Number(b.dataset.n) } })); }
    catch (e) { $("#mcq-area").innerHTML = ""; throw e; }
  })));
  $("#weekly").onclick = () => withButton($("#weekly"), async () => {
    busy($("#written"), "Your mentor is writing the questions…");
    await api("/api/test/weekly", { body: {} }); await refreshMe();
    $("#written").innerHTML = taskPanel(ME.progress.current); wireTask();
  });
}
function renderMcq(t) {
  const area = $("#mcq-area");
  area.innerHTML = `<section class="panel"><div class="panel-head"><h2>${esc(t.title)}</h2><span class="hint">${t.questions.length} questions</span></div>
    <form id="mcq">${t.questions.map((q, i) => `<fieldset class="q" style="border:0;margin:0;padding-inline:0"><legend class="q-n">Question ${i + 1}</legend>
      <div class="prose" style="margin-top:4px">${md(q.q)}</div>
      ${q.options.map((o, j) => `<label class="opt" for="q${i}o${j}"><input type="radio" id="q${i}o${j}" name="q${i}" value="${j}"><span>${esc(o)}</span></label>`).join("")}
      <div class="explain" id="ex${i}" hidden></div></fieldset>`).join("")}
    <div class="row" style="margin-top:10px"><button class="btn primary" type="submit">Mark my answers</button><span class="hint" id="mcq-hint"></span></div></form>
    <div id="mcq-result"></div></section>`;
  $("#mcq").onsubmit = (e) => {
    e.preventDefault();
    const answers = t.questions.map((_, i) => { const c = $(`input[name=q${i}]:checked`); return c ? Number(c.value) : null; });
    const blank = answers.filter((a) => a === null).length;
    if (blank && !$("#mcq").dataset.warned) { $("#mcq").dataset.warned = "1"; $("#mcq-hint").textContent = `${plural(blank, "question")} still blank. Press again to mark anyway.`; return; }
    withButton(e.submitter, async () => {
      const r = await api("/api/mcq/grade", { body: { id: t.id, answers } });
      r.results.forEach((res, i) => {
        $$(`input[name=q${i}]`).forEach((inp, j) => {
          inp.disabled = true;
          if (j === res.answer) inp.closest(".opt").classList.add("right"); else if (j === res.given) inp.closest(".opt").classList.add("wrong");
        });
        const ex = $(`#ex${i}`); ex.hidden = false; ex.classList.add(res.correct ? "ok" : "no");
        ex.innerHTML = `<strong>${res.correct ? "Right." : "Not quite."}</strong> ${md(res.explain)}`;
      });
      e.submitter.hidden = true; $("#mcq-hint").textContent = "";
      $("#mcq-result").innerHTML = `<div class="stack" style="margin-top:14px;padding-top:16px;border-top:1px solid var(--rule)">
        <div class="row"><span class="score-big">${r.correct}/${r.total}</span><span class="muted">${r.score >= 8 ? "Strong result." : r.score >= 5 ? "Decent. Read the explanations for the ones you missed." : "Worth revising before moving on."}</span></div>
        ${r.ticked?.length ? `<p class="note good">Passed. Your mentor ticked ${plural(r.ticked.length, "topic")} on your <a href="#progress">progress list</a>.</p>` : ""}
        <div class="row"><button class="btn" type="button" id="mcq-done">Done</button></div></div>`;
      $("#mcq-done").onclick = () => tests();
      await refreshMe();
    });
  };
}

// ---------------------------------------------------------------- Articles
const LABEL_TEXT = { established: "established practice", modern: "current practice", emerging: "emerging" };
async function articles(args) {
  const filter = { week: 0, source: "", ...(articles.filter || {}) };
  const qs = new URLSearchParams();
  if (filter.week) qs.set("week", filter.week);
  if (filter.source) qs.set("source", filter.source);
  const d = await api(`/api/articles?${qs}`);
  const weekTitle = (w) => ME.progress.modules[w - 1]?.title || "";
  view.innerHTML = `
    <header class="page-head"><span class="label">Articles</span><h1>What's new in data.</h1>
      <p class="muted" style="max-width:68ch">Fresh posts from the Databricks, Snowflake, Microsoft, AWS, Google Cloud and dbt blogs and a few
      independent writers. Your mentor reads each one, sums it up in plain words and links it to the week of the course it belongs to.</p></header>
    <section class="panel stack">
      <div class="filters" role="group" aria-label="Show">
        <button class="filter" data-f="all" aria-pressed="${!filter.week && !filter.source}">Everything</button>
        <button class="filter" data-f="week" aria-pressed="${filter.week === d.current_week && !filter.source}">Your week (${d.current_week})</button>
        ${d.sources.map((s) => `<button class="filter" data-f="source" data-v="${esc(s)}" aria-pressed="${filter.source === s}">${esc(s)}</button>`).join("")}
      </div>
      ${filter.week ? `<p class="hint">Showing articles for week ${filter.week}: ${esc(weekTitle(filter.week))}.</p>` : ""}
      <div class="feed">${d.items.length ? d.items.map((a) => `
        <article class="article ${a.new ? "is-new" : ""}">
          <div class="when"><b>${esc(a.source)}</b>${esc(fmtDate(a.published))}</div>
          <div>
            <h3><a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.title)}</a></h3>
            <p>${esc(a.summary)}</p>
            <div class="meta">
              <span class="chip ${a.label === "emerging" ? "warn" : a.label === "established" ? "" : "blue"}">${esc(LABEL_TEXT[a.label] || a.label)}</span>
              ${a.week ? `<button class="chip" style="cursor:pointer" data-week="${a.week}" title="${esc(weekTitle(a.week))}">week ${a.week}</button>` : ""}
              ${(a.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("")}
            </div>
          </div>
        </article>`).join("")
        : `<p class="empty">${d.last_fetch ? "Nothing here yet for this filter. Try Everything." : "The first batch of articles is being collected. Check back in a few minutes."}</p>`}</div>
      <p class="hint">${d.last_fetch ? `Last checked ${new Date(d.last_fetch).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}. New posts are picked up every few hours.` : ""}</p>
    </section>
    ${ME.role === "admin" ? `<section class="panel" id="sources"><div class="panel-head"><h2>Sources</h2><button class="btn sm" id="refresh">Check for new articles now</button></div>
      <div id="src-list" class="hint">Loading…</div>
      <form class="row" id="src-add" style="margin-top:12px"><input id="src-name" placeholder="Name (optional)" style="width:180px"><input id="src-url" placeholder="https://example.com/feed.xml" style="flex:1;min-width:220px"><button class="btn" type="submit">Add feed</button></form>
      <p class="hint">Any blog's RSS or Atom feed works. Unrelated or promotional posts are filtered out automatically.</p></section>` : ""}`;

  $$("[data-f]").forEach((b) => (b.onclick = () => {
    articles.filter = b.dataset.f === "week" ? { week: d.current_week } : b.dataset.f === "source" ? { source: b.dataset.v } : {};
    articles();
  }));
  $$("[data-week]").forEach((b) => (b.onclick = () => { articles.filter = { week: Number(b.dataset.week) }; articles(); }));
  if (ME.articles_new) { api("/api/articles/seen", { body: {} }).then(refreshMe).catch(() => {}); }
  if (ME.role === "admin") wireSources();
}
async function wireSources() {
  const draw = async () => {
    const s = await api("/api/admin/articles/sources");
    $("#src-list").className = "";
    $("#src-list").innerHTML = s.sources.map((x) => `<div class="source-row">
      <input type="checkbox" id="src-${esc(x.id)}" data-src="${esc(x.id)}" ${x.on ? "checked" : ""} title="On or off">
      <label for="src-${esc(x.id)}" style="min-width:0"><strong>${esc(x.name)}</strong><div class="url">${esc(x.url)}</div></label>
      <span class="hint num">${plural(x.count, "article")}</span>
      <button class="btn sm quiet danger" data-del="${esc(x.id)}">Remove</button></div>`).join("");
    $$("[data-src]").forEach((c) => (c.onchange = () => withButton(null, async () => {
      await api(`/api/admin/articles/sources/${c.dataset.src}`, { body: { on: c.checked } }); toast(c.checked ? "Source on." : "Source off.");
    })));
    $$("[data-del]").forEach((b) => (b.onclick = () => confirmInline(b, "Remove this source?", "Remove", async () => {
      await api(`/api/admin/articles/sources/${b.dataset.del}`, { method: "DELETE" }); draw();
    })));
  };
  await draw();
  $("#src-add").onsubmit = (e) => {
    e.preventDefault();
    withButton(e.submitter, async () => {
      const r = await api("/api/admin/articles/sources", { body: { name: $("#src-name").value, url: $("#src-url").value.trim() } });
      $("#src-name").value = $("#src-url").value = ""; toast(`Added ${r.name}. Press "Check for new articles now" to pull from it.`); draw();
    });
  };
  const rb = $("#refresh");
  rb.onclick = () => withButton(rb, async () => {
    rb.textContent = "Checking… (up to a minute)";
    try {
      const r = await api("/api/admin/articles/refresh", { body: {} });
      toast(r.added ? `${plural(r.added, "new article")} added.` : "No new articles right now.");
      await refreshMe(); articles();
    } finally { rb.textContent = "Check for new articles now"; }
  });
}

// ---------------------------------------------------------------- Ask (saved chats, live answers)
const STARTERS = ["When should I not use a star schema?", "Show me SCD type 2 with a Delta MERGE",
  "Data Vault or Kimball for a bank?", "Explain medallion architecture like I'm new"];
const HISTORY_ICON = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>`;
const fmtWhen = (iso) => {
  const d = new Date(iso), now = new Date();
  return d.toDateString() === now.toDateString() ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
};

async function ask() {
  view.innerHTML = `
    <header class="page-head">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:12px">
        <div class="stack" style="gap:4px;min-width:0"><span class="label">Ask</span><h1>Ask your mentor.</h1></div>
        <div class="row" style="gap:6px;flex-wrap:nowrap">
          <button class="btn sm" id="chat-history" type="button" aria-expanded="false">${HISTORY_ICON}<span>History</span></button>
          <button class="btn sm primary" id="chat-new" type="button">+ New chat</button>
        </div>
      </div>
      <p class="muted" id="chat-title">Concepts, a design you're stuck on (leave out company names and real data), interview practice, anything in data.</p>
    </header>
    <section class="panel" id="history-panel" hidden>
      <div class="panel-head"><h2>Your chats</h2><button class="btn sm quiet" id="history-close" type="button">Close</button></div>
      <div id="history-list" class="stack" style="gap:0"></div>
    </section>
    <div class="thread" id="thread"></div>
    <div class="starters" id="starters"></div>
    <form class="composer" id="composer"><label for="q" class="label" style="position:absolute;left:-9999px">Your question</label>
      <textarea id="q" placeholder="Type a question. Enter sends, Shift+Enter adds a line."></textarea>
      <button class="btn primary" type="submit" id="send">Send</button></form>`;

  const thread = $("#thread");
  let chatId = null, sending = false;
  const add = (role, text) => {
    const d = document.createElement("div");
    if (role === "user") { d.className = "turn-me"; d.textContent = text; }
    else { d.className = "turn-ai"; d.innerHTML = `<div class="who">Mentor</div><div class="prose">${md(text)}</div>`; }
    thread.appendChild(d); return d;
  };
  const nearBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
  const toBottom = () => window.scrollTo(0, document.body.scrollHeight);
  const showStarters = () => {
    $("#starters").innerHTML = "";
    STARTERS.forEach((s) => { const b = document.createElement("button"); b.className = "btn sm"; b.type = "button"; b.textContent = s;
      b.onclick = () => { $("#q").value = s; send(); }; $("#starters").appendChild(b); });
  };
  const setTitle = (t) => { $("#chat-title").textContent = t || "New chat. Ask anything in data."; };

  async function openChat(id) {
    try {
      const c = await api(`/api/chats/${encodeURIComponent(id)}`);
      chatId = c.id; store.set("chat:current", c.id);
      thread.innerHTML = ""; $("#starters").innerHTML = "";
      c.messages.forEach((m) => add(m.role, m.text));
      setTitle(c.title);
      if (!c.messages.length) showStarters();
      toBottom();
    } catch (_) { startNew(); }
  }
  function startNew() {
    chatId = null; store.del("chat:current");
    thread.innerHTML = ""; setTitle(""); showStarters(); $("#q").focus();
  }
  async function drawHistory() {
    const { chats } = await api("/api/chats");
    $("#history-list").innerHTML = chats.length ? chats.map((c) => `
      <div class="row" style="justify-content:space-between;gap:10px;padding:10px 0;border-top:1px solid var(--rule);flex-wrap:nowrap">
        <button type="button" class="btn quiet" data-open="${esc(c.id)}" style="flex:1;min-width:0;justify-content:flex-start;text-align:left;padding:4px 6px;${c.id === chatId ? "background:var(--blue-wash)" : ""}">
          <span style="min-width:0"><strong style="font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.title)}</strong>
          <span class="hint">${esc(fmtWhen(c.updated))} · ${plural(Math.ceil(c.count / 2), "question")}</span></span></button>
        <button type="button" class="btn sm quiet danger" data-del="${esc(c.id)}" aria-label="Delete this chat">Delete</button>
      </div>`).join("") : `<p class="empty">No saved chats yet. Your conversations will appear here.</p>`;
    $$("[data-open]", $("#history-list")).forEach((b) => (b.onclick = () => { togglePanel(false); openChat(b.dataset.open); }));
    $$("[data-del]", $("#history-list")).forEach((b) => (b.onclick = () => confirmInline(b, "Delete?", "Delete", async () => {
      await api(`/api/chats/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
      if (b.dataset.del === chatId) startNew();
      drawHistory();
    })));
  }
  function togglePanel(open) {
    $("#history-panel").hidden = !open;
    $("#chat-history").setAttribute("aria-expanded", String(open));
    if (open) { drawHistory().catch((e) => toast(e.message)); $("#history-panel").scrollIntoView({ behavior: "smooth" }); }
  }

  async function send() {
    const q = $("#q").value.trim(); if (!q || sending) return;
    sending = true; $("#send").disabled = true;
    $("#q").value = ""; $("#starters").innerHTML = "";
    add("user", q);
    const w = add("model", ""), out = $(".prose", w);
    busy(out, "Thinking…"); toBottom();
    try {
      if (!chatId) { const c = await api("/api/chats", { body: {} }); chatId = c.id; store.set("chat:current", c.id); }
      let r;
      try {
        r = await fetch(`/api/chats/${encodeURIComponent(chatId)}/message`, {
          method: "POST", headers: { "X-Mentor": "1", "Content-Type": "application/json" }, body: JSON.stringify({ message: q }) });
      } catch (_) { throw new Error("Can't reach the mentor right now. Check your internet, or try again in a minute."); }
      if (r.status === 401) { showSignin(); throw new Error("Please sign in again."); }
      if (!r.ok || r.headers.get("ngrok-error-code")) {
        let m = ""; try { m = (await r.json()).detail; } catch (_) {}
        throw new Error(typeof m === "string" && m ? m : "Mentor is reconnecting. Try again in a minute; your chat is saved.");
      }
      const reader = r.body.getReader(), dec = new TextDecoder();
      let text = "", queued = false;
      const paint = () => { queued = false; const stick = nearBottom(); out.innerHTML = md(text.split("\n\u0000ERROR:")[0]); if (stick) toBottom(); };
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        text += dec.decode(value, { stream: true });
        if (!queued) { queued = true; requestAnimationFrame(paint); }
      }
      text += dec.decode();
      const [answer, err] = text.split("\n\u0000ERROR:");
      out.innerHTML = answer.trim() ? md(answer) : "";
      if (err) out.insertAdjacentHTML("beforeend", `<p class="error">${esc(err)}</p>`);
      if (!err && thread.querySelectorAll(".turn-me").length === 1) setTitle(q.split("\n")[0].slice(0, 70));
    } catch (e) {
      out.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    } finally {
      sending = false; $("#send").disabled = false;
      refreshMe().catch(() => {});
    }
  }

  $("#chat-new").onclick = () => { togglePanel(false); startNew(); };
  $("#chat-history").onclick = () => togglePanel($("#history-panel").hidden);
  $("#history-close").onclick = () => togglePanel(false);
  $("#composer").onsubmit = (e) => { e.preventDefault(); send(); };
  $("#q").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };

  // reopen the chat you were in; otherwise the most recent one
  const last = store.get("chat:current");
  if (last) return openChat(last);
  const { chats } = await api("/api/chats");
  if (chats.length) openChat(chats[0].id); else startNew();
}

// ---------------------------------------------------------------- People (admin)
async function people() {
  if (ME.role !== "admin") { location.hash = "#home"; return; }
  const d = await api("/api/admin/users");
  view.innerHTML = `
    <header class="page-head"><span class="label">People</span><h1>${plural(d.users.length, "learner")}.</h1>
      <p class="muted">Anyone can join by picking a username${CFG.require_invite ? ", as long as they have an invite code" : ""}. Everyone shares one AI allowance, so keep an eye on numbers.</p></header>
    ${d.public_url ? `<section class="panel"><div class="panel-head"><h2>Public link</h2><span class="hint">changes when the PC restarts</span></div>
      <div class="copy"><input id="pub-url" readonly value="${esc(d.public_url)}"><button type="button" class="btn sm" data-copy="pub-url">Copy</button></div>
      <p class="hint">Send this to learners. ${ME.telegram ? "You also get it on Telegram after each restart." : "Connect Telegram on the You page to get the new link sent to you after each restart."}</p></section>` : ""}
    <section class="panel"><div class="panel-head"><h2>Invite someone</h2></div>
      <form class="row" id="inv"><input id="inv-name" placeholder="Their name (optional)" style="flex:1;min-width:200px"><button class="btn primary" type="submit">Create invite</button></form>
      <div id="inv-out"></div>
      ${d.invites.length ? `<div class="stack" style="gap:6px"><span class="label">Open invites</span>${d.invites.map((i) => `<div class="row" style="justify-content:space-between"><span><code>${esc(i.code)}</code> ${esc(i.name || "")} <span class="hint">until ${esc(fmtDate(i.expires))}</span></span><button class="btn sm quiet" data-revoke="${esc(i.code)}">Cancel</button></div>`).join("")}</div>` : ""}
    </section>
    <section class="panel"><div class="table-wrap"><table class="table stackable"><thead><tr><th>Learner</th><th>Progress</th><th>Reminder</th><th></th></tr></thead><tbody>
    ${d.users.map((u) => `<tr data-uid="${esc(u.uid)}">
      <td><strong>${esc(u.name)}</strong> ${u.role === "admin" ? `<span class="chip blue">admin</span>` : ""}${u.paused ? ` <span class="chip warn">paused</span>` : ""}
        <div class="hint">${u.username ? "@" + esc(u.username) : "Telegram only"}${u.telegram && u.username ? " · Telegram" : ""}</div></td>
      <td class="num">${u.lessons}/${CFG.total_lessons} lessons · avg ${u.average ?? "–"} · ${plural(u.streak, "day")} streak${u.weak_areas.length ? `<div class="hint">revisit: ${u.weak_areas.map(esc).join(", ")}</div>` : ""}</td>
      <td class="num">${esc(u.reminder)}</td>
      <td style="white-space:nowrap"><button class="btn sm" data-edit>Edit</button> ${u.uid === ME.uid ? "" : `<button class="btn sm quiet danger" data-remove>Remove</button>`}</td></tr>
      <tr class="edit-row" hidden><td colspan="4"><form class="stack" style="padding:6px 0 10px">
        <div class="row" style="align-items:flex-end">
          <label class="field" style="flex:1;min-width:160px" for="n-${esc(u.uid)}"><span>Name</span><input id="n-${esc(u.uid)}" name="name" value="${esc(u.name)}" maxlength="40"></label>
          <label class="field" style="width:130px" for="t-${esc(u.uid)}"><span>Reminder</span><input id="t-${esc(u.uid)}" name="reminder" type="time" value="${esc(u.reminder)}"></label>
          <label class="field" style="width:130px" for="r-${esc(u.uid)}"><span>Role</span><select id="r-${esc(u.uid)}" name="role"><option value="member" ${u.role === "member" ? "selected" : ""}>Learner</option><option value="admin" ${u.role === "admin" ? "selected" : ""}>Admin</option></select></label>
          <label class="field" style="width:150px" for="p-${esc(u.uid)}"><span>Daily messages</span><select id="p-${esc(u.uid)}" name="paused"><option value="false" ${!u.paused ? "selected" : ""}>On</option><option value="true" ${u.paused ? "selected" : ""}>Paused</option></select></label>
        </div>
        <label class="field" for="b-${esc(u.uid)}"><span>Background</span><textarea id="b-${esc(u.uid)}" name="background" maxlength="600" style="min-height:70px">${esc(u.background)}</textarea></label>
        <div class="row"><button class="btn primary" type="submit">Save</button><button class="btn quiet" type="button" data-cancel>Cancel</button></div></form></td></tr>`).join("")}
    </tbody></table></div></section>`;
  $("#inv").onsubmit = (e) => {
    e.preventDefault();
    withButton(e.submitter, async () => {
      const r = await api("/api/admin/invite", { body: { name: $("#inv-name").value } });
      const copy = (label, v, id) => v ? `<label class="field" for="${id}"><span>${label}</span><div class="copy"><input id="${id}" readonly value="${esc(v)}"><button type="button" class="btn sm" data-copy="${id}">Copy</button></div></label>` : "";
      $("#inv-out").innerHTML = `<div class="stack"><p class="hint">Works once, for 7 days. Send whichever suits them.</p>
        ${copy("Website link", r.web, "c-web")}${copy("Telegram link", r.telegram, "c-tg")}${copy("Code", r.code, "c-code")}</div>`;
      $$("[data-copy]", $("#inv-out")).forEach((b) => (b.onclick = () => {
        const inp = $("#" + b.dataset.copy);
        navigator.clipboard.writeText(inp.value).then(() => toast("Copied.")).catch(() => { inp.select(); toast("Press Ctrl+C to copy."); });
      }));
    });
  };
  const pub = $("[data-copy=pub-url]");
  pub && (pub.onclick = () => navigator.clipboard.writeText($("#pub-url").value).then(() => toast("Copied.")).catch(() => { $("#pub-url").select(); toast("Press Ctrl+C to copy."); }));
  $$("[data-revoke]").forEach((b) => (b.onclick = () => withButton(b, async () => { await api(`/api/admin/invites/${encodeURIComponent(b.dataset.revoke)}`, { method: "DELETE" }); people(); })));
  $$("tr[data-uid]").forEach((tr) => {
    const uid = tr.dataset.uid, editRow = tr.nextElementSibling, name = $("strong", tr).textContent;
    $("[data-edit]", tr).onclick = () => (editRow.hidden = !editRow.hidden);
    $("[data-cancel]", editRow).onclick = () => (editRow.hidden = true);
    $("form", editRow).onsubmit = (e) => {
      e.preventDefault(); const f = e.target;
      withButton(e.submitter, async () => {
        await api(`/api/admin/users/${uid}`, { body: { name: f.name.value, reminder: f.reminder.value, role: f.role.value, paused: f.paused.value === "true", background: f.background.value } });
        toast("Saved."); if (uid === ME.uid) await refreshMe(); people();
      });
    };
    const rm = $("[data-remove]", tr);
    rm && (rm.onclick = () => confirmInline(rm, `Remove ${name}?`, "Remove", async () => {
      await api(`/api/admin/users/${uid}`, { method: "DELETE" }); toast(`${name} removed. Their progress is archived.`); people();
    }));
  });
}

// ---------------------------------------------------------------- You (profile + study reminders)
APP.inApp = () => !!window.MentorApp;   // running inside the Android app (native alarms)
async function profile() {
  const sched = ME.schedule, n = ME.notify;
  let times = [...sched.times];
  const tz = esc(CFG.timezone.replace("Asia/Kolkata", "India time"));
  view.innerHTML = `
    <header class="page-head"><span class="label">You</span><h1>${esc(ME.name)}</h1><p class="muted">Signed in as <span class="mono">@${esc(ME.username || "")}</span>. Use this username on any device to pick up where you left off.</p></header>
    <section class="panel stack" id="rem" style="gap:16px">
      <div class="panel-head"><h2>Study reminders</h2>${ME.paused ? `<span class="chip warn">paused</span>` : ""}</div>
      <p class="muted">Pick the times you want to study. ${APP.inApp() ? "Your phone rings and vibrates like an alarm at each one." : "You get a notification at each one."}
        The first one each day brings today's lesson; the others are a nudge to keep going.</p>
      <div>
        <span class="label">Times (${tz})</span>
        <div class="row" id="rm-times" style="margin-top:8px"></div>
        <div class="row" style="margin-top:10px">
          <label for="rm-new" class="label" style="position:absolute;left:-9999px">New reminder time</label>
          <input type="time" id="rm-new" value="12:30" style="width:140px">
          <button class="btn" type="button" id="rm-add">Add time</button>
          <span class="hint">Up to 6 a day.</span>
        </div>
      </div>
      <div class="stack" style="gap:2px">
        <span class="label">How it rings</span>
        ${[["sound", "Ring with sound", "Plays your phone's alarm sound"], ["vibrate", "Vibrate", "Buzzes even when the phone is on silent"]]
          .map(([k, t, h]) => `<label class="row" for="rp-${k}" style="gap:10px;align-items:flex-start;padding:6px 0"><input type="checkbox" id="rp-${k}" data-rp="${k}" style="width:18px;height:18px;margin-top:3px" ${n[k] ? "checked" : ""}><span><strong style="font-weight:600">${t}</strong><br><span class="hint">${h}</span></span></label>`).join("")}
      </div>
      <div class="stack" style="gap:2px">
        <span class="label">What they tell you</span>
        ${[["daily", "Today's lesson", "Lesson, Saturday test, Sunday revision"], ["nudge", "Waiting answers", "When an assignment or test still needs your answer"], ["articles", "New articles", "At most once a day, with the first reminder"]]
          .map(([k, t, h]) => `<label class="row" for="rp-${k}" style="gap:10px;align-items:flex-start;padding:6px 0"><input type="checkbox" id="rp-${k}" data-rp="${k}" style="width:18px;height:18px;margin-top:3px" ${n[k] ? "checked" : ""}><span><strong style="font-weight:600">${t}</strong><br><span class="hint">${h}</span></span></label>`).join("")}
      </div>
      <div class="row"><button class="btn primary" type="button" id="rm-save">Save reminders</button><span class="hint" id="rm-dirty"></span></div>
      <div class="stack" id="notif" style="gap:10px;padding-top:14px;border-top:1px solid var(--rule)">
        <div class="row" style="justify-content:space-between"><span class="label">On this device</span><span class="chip" id="notif-state">checking…</span></div>
        <div class="row" id="notif-actions"></div>
        <p class="hint" id="notif-note"></p>
      </div>
    </section>
    <form class="panel stack" id="pf" style="gap:18px">
      <label class="field" for="pf-name"><span>What should your mentor call you?</span><input id="pf-name" name="name" value="${esc(ME.name)}" maxlength="40"></label>
      <label class="field" for="pf-bg"><span>Your background and goal</span>
        <textarea id="pf-bg" name="background" maxlength="600" style="min-height:96px">${esc(ME.background)}</textarea>
        <span class="hint" style="font-weight:400">Lessons and reviews are pitched to this. For example: "3 years of SQL and ADF, learning Databricks, aiming for a data architect role."</span></label>
      <label class="row" for="pf-pause" style="gap:8px"><input type="checkbox" id="pf-pause" name="paused" style="width:auto" ${ME.paused ? "checked" : ""}><span>Pause all reminders for now (holidays, busy weeks)</span></label>
      <div class="row"><button class="btn primary" type="submit">Save changes</button></div>
    </form>
    ${installCard("profile")}
    <section class="panel"><h2>Telegram</h2>
      ${ME.telegram ? `<p>Connected. Your daily lesson arrives in Telegram at ${esc(sched.times[0])}, and you can answer there too.</p>`
        : CFG.telegram_bot ? `<p class="muted">Get each day's lesson as a Telegram message, with the same progress as here.</p><div class="row"><button class="btn" id="link">Connect Telegram</button></div>`
        : `<p class="muted">Telegram isn't set up on this server.</p>`}</section>
    <div class="row"><button class="btn quiet" id="logout">Sign out</button></div>`;

  const dirty = () => { $("#rm-dirty").textContent = "Unsaved changes"; };
  const drawTimes = () => {
    $("#rm-times").innerHTML = times.map((t) => `<span class="chip blue num" style="font-size:.9rem;padding:6px 6px 6px 10px">${esc(t)}
      <button type="button" class="btn sm quiet" data-rm="${esc(t)}" aria-label="Remove ${esc(t)}" style="padding:0 6px;font-size:1rem;line-height:1" ${times.length < 2 ? "disabled title='Keep at least one'" : ""}>×</button></span>`).join("");
    $$("[data-rm]").forEach((b) => (b.onclick = () => { times = times.filter((t) => t !== b.dataset.rm); drawTimes(); dirty(); }));
    $("#rm-add").disabled = times.length >= 6;
  };
  drawTimes();
  $("#rm-add").onclick = () => {
    const v = $("#rm-new").value;
    if (!v) return toast("Pick a time first.");
    if (times.includes(v)) return toast("That time is already in the list.");
    times = [...times, v].sort(); drawTimes(); dirty();
  };
  $$("[data-rp]").forEach((c) => (c.onchange = dirty));
  $("#rm-save").onclick = () => withButton($("#rm-save"), async () => {
    const body = { times }; $$("[data-rp]").forEach((x) => (body[x.dataset.rp] = x.checked));
    await api("/api/notify-prefs", { body });
    await refreshMe(); $("#rm-dirty").textContent = "";
    toast(APP.inApp() ? `Saved. Your phone will ring at ${times.join(", ")}.` : `Saved. Reminders at ${times.join(", ")}.`);
  });
  $("#pf").onsubmit = (e) => {
    e.preventDefault(); const f = e.target;
    withButton(e.submitter, async () => {
      await api("/api/profile", { body: { name: f.name.value, background: f.background.value, paused: f.paused.checked } });
      await refreshMe(); toast("Saved."); profile();
    });
  };
  const ln = $("#link");
  ln && (ln.onclick = () => withButton(ln, async () => {
    const r = await api("/api/link-telegram", { body: {} });
    ln.outerHTML = `<a class="btn primary" href="${esc(r.url)}" target="_blank" rel="noopener">Open Telegram to finish</a>`;
  }));
  $("#logout").onclick = async () => { await api("/api/logout", { body: {} }); location.href = "/"; };
  wireInstall();
  wireDelivery();
}
// the Android app calls this when it comes back to the front (e.g. after the learner changed a setting)
window.onAppResume = () => { if ($("#notif-actions")) wireDelivery(); };
/** Shows how reminders reach this device: native alarms in the Android app, web notifications elsewhere. */
async function wireDelivery() {
  const state = $("#notif-state"), actions = $("#notif-actions"), note = $("#notif-note");
  if (APP.inApp()) {
    let ok = true;
    try { ok = window.MentorApp.notificationsAllowed(); } catch (_) {}
    state.textContent = ok ? "alarms on" : "blocked"; state.className = "chip " + (ok ? "good" : "warn");
    let exact = true;
    try { exact = window.MentorApp.exactAlarmsAllowed ? window.MentorApp.exactAlarmsAllowed() : true; } catch (_) {}
    actions.innerHTML = !ok
      ? `<button class="btn primary" id="np-allow">Allow notifications</button>`
      : `<button class="btn" id="np-test">Ring a test alarm</button>${exact ? "" : `<button class="btn primary" id="np-exact">Ring on the exact minute</button>`}`;
    note.textContent = !ok ? "Android is blocking this app's notifications."
      : exact ? "Alarms are set on this phone, so they ring on time even without internet."
      : "Alarms are set, but Android may ring them a few minutes late. Tap “Ring on the exact minute” and switch on Allow.";
    $("#np-test") && ($("#np-test").onclick = () => { window.MentorApp.testAlarm(); toast("Ringing in a few seconds…"); });
    $("#np-allow") && ($("#np-allow").onclick = () => { window.MentorApp.openNotificationSettings(); });
    $("#np-exact") && ($("#np-exact").onclick = () => { window.MentorApp.openExactAlarmSettings(); });
    return;
  }
  if (!ME.push_ready) { state.textContent = "not available"; actions.innerHTML = ""; return; }
  let on = false;
  try { on = !!(await currentSubscription()) && Notification.permission === "granted"; } catch (_) {}
  state.textContent = on ? "on" : "off"; state.className = "chip " + (on ? "good" : "");
  note.textContent = ME.push_devices > (on ? 1 : 0) ? `Also on for ${plural(ME.push_devices - (on ? 1 : 0), "other device")}.` : "";
  if (APP.pushSupported() && Notification.permission === "denied") {
    actions.innerHTML = `<p class="hint">Notifications are blocked for this site in your browser. Open the site settings (the icon left of the address), allow notifications, then reload this page.</p>`;
    return;
  }
  if (APP.pushSupported() && !(await swReady(2500))) {
    actions.innerHTML = `<p class="hint">This browser can't show notifications. Chrome, Edge, Firefox or Safari can, or install the Android app.</p>`;
    return;
  }
  if (!APP.pushSupported()) {
    actions.innerHTML = `<p class="hint">${APP.ios() && !APP.standalone()
      ? "On iPhone, add the app to your Home Screen first (Share, then Add to Home Screen), open it from there, and turn notifications on here."
      : "This browser can't show notifications from websites. Chrome, Edge, Firefox or Safari can."}</p>`;
    return;
  }
  actions.innerHTML = on
    ? `<button class="btn" id="np-test">Send a test</button><button class="btn quiet" id="np-off">Turn off on this device</button>`
    : `<button class="btn primary" id="np-on">Turn on notifications</button>`;
  $("#np-on") && ($("#np-on").onclick = () => withButton($("#np-on"), async () => { await enablePush(); await refreshMe(); toast("Notifications on. A test one is on its way."); wireDelivery(); }));
  $("#np-off") && ($("#np-off").onclick = () => withButton($("#np-off"), async () => { await disablePush(); await refreshMe(); toast("Turned off on this device."); wireDelivery(); }));
  $("#np-test") && ($("#np-test").onclick = () => withButton($("#np-test"), async () => { await api("/api/push/test", { body: {} }); toast("Sent. It should pop up in a few seconds."); }));
}

boot().then(() => { if (!ME) showSignin(); });
