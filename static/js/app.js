// ConfView dashboard. The data is baked into the page by Hugo as JSON blocks
// (see layouts/index.html); this script renders the timeline and the cards.
// It is a separate file, not inline, so the Content-Security-Policy can be
// script-src 'self'.
"use strict";

const readJSON = id => {
  const el = document.getElementById(id);
  try { return el ? JSON.parse(el.textContent) : null; } catch (e) { return null; }
};
const DATA   = readJSON("cv-data") || {};
const TIERS  = readJSON("cv-tiers") || [];
const PARAMS = readJSON("cv-params") || {};
const TIER_BY_ID = Object.fromEntries(TIERS.map(t => [String(t.id), t]));
const PREFERRED_REGION = PARAMS.preferredRegion || "";

// Normalise once: every entry the page can show has conference start/end and a
// stages object. Entries without conference dates are left out of everything,
// including the "tracked" count, and reported in the console.
const CONFS = (Array.isArray(DATA.conferences) ? DATA.conferences : []).filter(c => {
  const ok = c && c.conference && c.conference.start && c.conference.end;
  if (!ok) console.warn("ConfView: skipping entry without conference dates:", c && (c.id || c.name));
  return ok;
}).map(c => ({ ...c, stages: c.stages || {}, topics: Array.isArray(c.topics) ? c.topics : [] }));

const STAGE_META = [
  { key:"submission",   name:"Submission",   ico:"📝" },
  { key:"notification", name:"Notification", ico:"📢" },
  { key:"camera_ready", name:"Camera-Ready", ico:"🖨" },
];

// "Today" at local noon. Recomputed on every render so a tab left open
// overnight does not show stale countdowns.
let TODAY;
const today = () => { const d = new Date(); d.setHours(12,0,0,0); return d; };
const parse = d => new Date(d+"T12:00:00");
const fmt = d => parse(d).toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"});
const fmtShort = d => parse(d).toLocaleDateString("en-US",{month:"short",day:"numeric"});
const utcDay = dt => Date.UTC(dt.getFullYear(), dt.getMonth(), dt.getDate());
const days = d => Math.round((utcDay(parse(d)) - utcDay(TODAY))/86400000);
// Data values go into innerHTML below, so escape them; status doubles as a CSS class.
const esc = v => String(v ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const statusOf = st => st && st.status === "confirmed" ? "confirmed" : "estimated";
const hexColor = v => typeof v === "string" && /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v) ? v : null;
// Decorative emoji: hidden from screen readers.
const ico = e => `<span aria-hidden="true">${e}</span>`;

// Tier chip colours as CSS custom properties; the stylesheet picks the light
// or dark pair. Only hex colours are accepted, so tiers.yaml cannot inject CSS.
function tierStyle(t){
  if (!t) return "";
  const vars = [["--tier-fg", t.color], ["--tier-bg", t.bg], ["--tier-fg-d", t.color_dark], ["--tier-bg-d", t.bg_dark]]
    .map(([k, v]) => hexColor(v) ? `${k}:${v}` : "").filter(Boolean);
  return vars.length ? ` style="${vars.join(";")}"` : "";
}

// Link to the call for papers: the edition's own page if the template gives
// one, else the first listed source. http(s) only.
function cfpUrl(c){
  const tpl = c.url_template && c.edition_year ? String(c.url_template).replace("{year}", c.edition_year) : null;
  const url = tpl || (Array.isArray(c.sources) && c.sources[0]);
  return url && /^https?:\/\//i.test(url) ? url : null;
}

// A conference outside the preferred region is flagged only for tiers that opt
// in via data/tiers.yaml (flag_outside_region).
function isOutsideRegion(c){
  const t = TIER_BY_ID[String(c.tier)];
  if (!t || !t.flag_outside_region || !PREFERRED_REGION) return false;
  return c.region && c.region !== PREFERRED_REGION && c.region !== "TBD";
}

// Submission date as a Date, or null if missing/invalid (refresh.py's
// _submission_date() does the same for its listing).
function submissionDate(c){
  const d = c.stages.submission && c.stages.submission.date;
  if (!d) return null;
  const dt = parse(d);
  return isNaN(dt) ? null : dt;
}

// Free-text search: every whitespace-separated term must appear somewhere in
// the conference's name, full name, location, region, tier or topics.
// Negative regions such as "Non-Europe" are left out, so that searching
// "europe" does not match them.
function matchesSearch(c, terms){
  if (!terms.length) return true;
  const region = /^non-/i.test(c.region || "") ? "" : c.region;
  const hay = [c.name, c.full_name, c.location, region, c.tier, ...c.topics]
    .filter(Boolean).join(" ").toLowerCase();
  return terms.every(t => hay.includes(t));
}

// Conferences to show: future deadlines only (unless includePast), optional
// tier/topic/search filters, soonest submission first.
function upcoming(includePast, tier, topic, query){
  const terms = (query || "").toLowerCase().split(/\s+/).filter(Boolean);
  tier = tier || null;
  topic = topic ? topic.toLowerCase() : null;
  const rows = CONFS.filter(c => {
    const due = submissionDate(c) || parse(c.conference.end);
    if (!includePast && due < TODAY) return false;
    if (tier && String(c.tier || "") !== tier) return false;
    if (topic && !c.topics.map(t => String(t).toLowerCase()).includes(topic)) return false;
    return matchesSearch(c, terms);
  });
  rows.sort((a,b) => (submissionDate(a) || new Date(8.64e15)) - (submissionDate(b) || new Date(8.64e15)));
  return rows;
}

function allTopics(){
  return [...new Set(CONFS.flatMap(c => c.topics))].sort();
}

const $ = id => document.getElementById(id);

function filtersActive(){
  return Boolean($("search").value.trim() || $("tierFilter").value || $("topicFilter").value);
}

function clearFilters(){
  $("search").value = ""; $("tierFilter").value = ""; $("topicFilter").value = "";
  main();
  $("search").focus();
}

function main(){
  TODAY = today();
  const showPast = $("showPast").checked;
  const confs = upcoming(showPast, $("tierFilter").value, $("topicFilter").value, $("search").value);

  $("stats").textContent =
    `${confs.length} shown of ${CONFS.length} tracked · data updated ${DATA.last_updated || "—"}`;

  const content = $("content");
  if (!confs.length){
    let title, body, clear = false;
    if (filtersActive()){
      title = "No conferences match these filters";
      body = "Try a shorter search, or clear the tier and topic filters.";
      clear = true;
    } else if (!showPast && CONFS.length){
      title = "No upcoming submission deadlines";
      body = "Every tracked conference's paper deadline has passed. Tick “Show past deadlines” to see the full list.";
    } else {
      title = "No conferences tracked yet";
      body = "Add entries to data/conferences.json and rebuild the site.";
    }
    content.innerHTML = `<div class="empty"><h2>${title}</h2><p>${body}</p>${
      clear ? '<button type="button" class="btn" id="clearFilters">Clear filters</button>' : ""}</div>`;
    if (clear) $("clearFilters").addEventListener("click", clearFilters);
    return;
  }
  content.innerHTML = `
    <h2 class="section-title" id="timeline-title">Timeline</h2>
    <a class="skip" href="#details-title">Skip timeline</a>
    <div class="timeline" role="group" aria-labelledby="timeline-title"><div class="tl-inner" id="timeline"></div></div>
    <h2 class="section-title" id="details-title" tabindex="-1">Details</h2>
    <div class="cards" id="cards"></div>`;
  renderTimeline(confs);
  renderCards(confs);
}

function populateTopicFilter(){
  const el = $("topicFilter");
  allTopics().forEach(t => {
    const opt = document.createElement("option"); opt.value = t; opt.textContent = t; el.appendChild(opt);
  });
}

function axisRange(confs){
  const ds = [];
  confs.forEach(c => { ds.push(submissionDate(c) || parse(c.conference.start), parse(c.conference.end)); });
  let mn = new Date(Math.min(...ds)), mx = new Date(Math.max(...ds));
  mn = new Date(mn.getFullYear(), mn.getMonth(), 1);
  mx = new Date(mx.getFullYear(), mx.getMonth()+1, 1);
  return [mn, mx];
}

// Timeline markers are focusable (keyboard and tap) and carry their text as
// an aria-label; the visual tooltip shows on hover and on focus.
function marker(cls, left, label, inner){
  const el = document.createElement("div");
  el.className = cls; el.style.left = left;
  el.tabIndex = 0;
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", label);
  el.innerHTML = `${inner}<div class="tip" aria-hidden="true">${esc(label)}</div>`;
  return el;
}

function renderTimeline(confs){
  const tl = $("timeline");
  const [mn, mx] = axisRange(confs);
  const span = mx - mn;
  const pct = d => (((d instanceof Date ? d : parse(d)) - mn)/span)*100;

  const axis = document.createElement("div"); axis.className="tl-axis"; axis.setAttribute("aria-hidden","true");
  let m = new Date(mn);
  while(m < mx){
    const g=document.createElement("div"); g.className="tl-grid"; g.style.left=pct(m)+"%"; axis.appendChild(g);
    const lab=document.createElement("div"); lab.className="tl-month"; lab.style.left=pct(m)+"%";
    lab.textContent=m.toLocaleDateString("en-US",{month:"short",year:"2-digit"}); axis.appendChild(lab);
    m = new Date(m.getFullYear(), m.getMonth()+1, 1);
  }
  tl.appendChild(axis);

  confs.forEach(c=>{
    const title = `${c.name || ""} ${c.edition_year || ""}`.trim();
    const row=document.createElement("div"); row.className="tl-row";
    const lab=document.createElement("div"); lab.className="tl-label";
    lab.innerHTML=`${esc(title)}<span class="loc" title="${esc(c.location)}">${ico("📍")} ${esc(c.location)}</span>`;
    row.appendChild(lab);
    const track=document.createElement("div"); track.className="tl-track";
    const lane=document.createElement("div"); lane.className="tl-lane"; track.appendChild(lane);
    STAGE_META.forEach(s=>{
      const st=c.stages[s.key]; if(!st||!st.date) return;
      track.appendChild(marker("milestone "+statusOf(st), pct(st.date)+"%",
        `${title} ${s.name}: ${fmt(st.date)} · ${statusOf(st)}`, '<div class="m-dot"></div>'));
    });
    const l=pct(c.conference.start), r=pct(c.conference.end);
    const cb = marker("confbar", l+"%",
      `${title} conference: ${fmt(c.conference.start)} – ${fmt(c.conference.end)}`, "");
    cb.style.width=Math.max(r-l,1.4)+"%";
    track.appendChild(cb);
    row.appendChild(track);
    tl.appendChild(row);
  });

  if (TODAY >= mn && TODAY <= mx){
    const tline=document.createElement("div"); tline.className="today-line"; tline.setAttribute("aria-hidden","true");
    tline.style.left=`calc(160px + (100% - 160px) * ${pct(TODAY)/100})`;
    tl.appendChild(tline);
  }
}

function renderCards(confs){
  const el = $("cards");
  // The "next" card is the first one whose paper deadline is still ahead
  // (with past deadlines shown, the list starts with passed ones).
  const next = confs.find(c => { const s = submissionDate(c); return s && s >= TODAY; });
  confs.forEach(c=>{
    const flagged = isOutsideRegion(c);
    const card=document.createElement("article");
    card.className=["card", c===next ? "next" : "", flagged ? "flagged" : ""].filter(Boolean).join(" ");

    const tierDef = TIER_BY_ID[String(c.tier)];
    const coreText = esc(tierDef ? (tierDef.short || tierDef.id) : (c.tier || "—"));
    const regionCls = flagged ? "region warn" : "region";
    const regionText = flagged ? `${coreText} outside ${esc(PREFERRED_REGION)}` : esc(c.region || "Region TBD");
    const sub = c.stages.submission && c.stages.submission.date;
    const dleft = sub ? days(sub) : null;
    const cdCls = dleft!==null && dleft>=0 && dleft<=21 ? "countdown soon" : "countdown";
    const title = `${esc(c.name)} ${esc(c.edition_year)}`;
    const url = cfpUrl(c);

    let html=`<h3>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${title}<span class="ext" aria-hidden="true">↗</span><span class="sr-only"> (call for papers, opens in a new tab)</span></a>` : title}</h3>
      ${c.full_name ? `<div class="full">${esc(c.full_name)}</div>` : ""}
      <div class="meta">${ico("📍")} ${esc(c.location)} <span class="core tier-chip"${tierStyle(tierDef)}>Tier ${coreText}</span>
      <span class="${regionCls}">${regionText}</span>
      <span class="${cdCls}">${dleft===null?"no paper deadline":dleft>=0?dleft+" days to submit":"deadline passed"}</span></div>`;
    if (c.topics.length){
      html += `<ul class="topics" aria-label="Topics">${c.topics.map(t=>`<li class="topic">${esc(t)}</li>`).join("")}</ul>`;
    }
    const rows=[
      ...STAGE_META.map(s=>({...s, st:c.stages[s.key]})),
      {key:"conference", name:"Conference", ico:"🎤",
       st:{date:c.conference.start, end:c.conference.end, status:c.conference.status}},
    ];
    let nu=null, nut=Infinity;
    rows.forEach(r=>{ if(!r.st?.date) return; const t=parse(r.st.date)-TODAY; if(t>=0&&t<nut){nut=t;nu=r.key;} });
    rows.forEach(r=>{
      if(!r.st?.date) return;
      const cls=["stage"]; if(r.key===nu) cls.push("nextup");
      const dateTxt = r.key==="conference"
        ? `${fmt(r.st.date)} – ${fmtShort(r.st.end)}` : fmt(r.st.date);
      const status = statusOf(r.st);
      const badge = (r.key==="conference" && status==="confirmed") ? "" :
        `<span class="mini ${status}" title="${status}"><span aria-hidden="true">${status==="confirmed"?"✓ conf":"≈ est"}</span><span class="sr-only">${status}</span></span>`;
      html+=`<div class="${cls.join(' ')}">
        <div class="s-name">${ico(r.ico)}${r.name}</div>
        <div class="s-right">${badge}<span class="s-date">${dateTxt}</span></div></div>`;
    });
    card.innerHTML=html; el.appendChild(card);
  });
}

// Theme toggle: cycles system -> light -> dark -> system. theme.js (loaded in
// <head>) already applied the stored choice before first paint.
const THEME_NEXT = { system:"light", light:"dark", dark:"system" };
const THEME_NAME = { system:"System", light:"Light", dark:"Dark" };
function syncThemeButton(){
  const mode = document.documentElement.getAttribute("data-theme-mode") || "system";
  const shown = document.documentElement.getAttribute("data-theme");
  const label = `Theme: ${THEME_NAME[mode]}${mode==="system" ? ` (${shown})` : ""}. Switch to ${THEME_NAME[THEME_NEXT[mode]].toLowerCase()}`;
  const btn = $("themeToggle");
  btn.setAttribute("aria-label", label);
  btn.title = label;
}
function setupTheme(){
  const T = window.cvTheme;
  if (!T) return;
  syncThemeButton();
  $("themeToggle").addEventListener("click", () => {
    const next = THEME_NEXT[T.stored()];
    T.save(next); T.apply(next); syncThemeButton();
  });
  if (T.mq) T.mq.addEventListener("change", () => {
    if (T.stored() === "system"){ T.apply("system"); syncThemeButton(); }
  });
}

let searchTimer;
populateTopicFilter();
setupTheme();
$("showPast").addEventListener("change", main);
$("tierFilter").addEventListener("change", main);
$("topicFilter").addEventListener("change", main);
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(main, 120); });
// Re-render when the tab comes back on a new day, so countdowns stay right.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && today().getTime() !== TODAY.getTime()) main();
});
main();
