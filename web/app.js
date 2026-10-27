"use strict";

// ---------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 1) => (x == null || isNaN(x) ? "–" : (100 * x).toFixed(d) + "%");
const spct = (x, d = 1) => (x == null || isNaN(x) ? "–" : (x >= 0 ? "+" : "") + (100 * x).toFixed(d) + "%");
const num = (x, d = 2) => (x == null || isNaN(x) ? "–" : Number(x).toFixed(d));
const money = (x) => (x == null || isNaN(x) ? "–" : Number(x).toLocaleString(undefined, { maximumFractionDigits: 2 }));
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const formHtml = (f) => `<span class="form">${[...(f || "")].map((c) => `<span class="${c}">${c}</span>`).join("")}</span>`;
const charts = {};

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

function store(k, v) { try { localStorage.setItem("betit." + k, v); } catch (_) {} }
function load(k) { try { return localStorage.getItem("betit." + k); } catch (_) { return null; } }

function staking() {
  return {
    bankroll: +$("#bankroll").value || 1000,
    kelly_mult: +$("#kelly").value,
    cap: +$("#cap").value,
    edge_min: +$("#edge").value,
  };
}

function chart(id, cfg) {
  if (charts[id]) charts[id].destroy();
  const css = getComputedStyle(document.documentElement);
  Chart.defaults.color = css.getPropertyValue("--muted").trim();
  Chart.defaults.borderColor = css.getPropertyValue("--line").trim();
  charts[id] = new Chart(document.getElementById(id), cfg);
}

// ------------------------------------------------------------------- tabs
function showTab(name) {
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  store("tab", name);
  if (name === "backtest") loadBacktests();
}
$$("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$$("[data-goto]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.goto); }));

// --------------------------------------------------------------- settings
["bankroll", "kelly", "cap", "edge"].forEach((id) => {
  const v = load(id);
  if (v) $("#" + id).value = v;
  $("#" + id).addEventListener("change", () => { store(id, $("#" + id).value); loadBoard(); });
});

// ------------------------------------------------------------------ status
async function loadStatus() {
  const s = await api("/api/status");
  const parts = [];
  if (s.football) parts.push(`Football: ${s.football.matches.toLocaleString()} matches to ${s.football.latest}`);
  else parts.push("Football: no data, run <code>python -m betit update</code>");
  if (s.tennis) parts.push(`Tennis: ${s.tennis.matches.toLocaleString()} matches to ${s.tennis.latest}`);
  parts.push(`${s.fixtures} upcoming fixtures`);
  $("#status").innerHTML = parts.join(" · ");
}

// ------------------------------------------------------------ value board
async function loadBoard() {
  const st = staking();
  const q = new URLSearchParams(st).toString();
  let b;
  try { b = await api("/api/football/board?" + q); } catch (e) { $("#board").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  if (!b.fixtures.length) {
    $("#board").innerHTML = `<p class="muted">${esc(b.message || "No upcoming fixtures.")}</p>`;
    return;
  }
  const rows = [];
  let flagged = 0;
  for (const f of b.fixtures) {
    const bets = f.bets.map((x) => `<span class="pill good">${esc(x.label)} @ ${num(x.odds)} · edge ${spct(x.price_edge)} · stake ${money(x.stake)}</span>`).join(" ");
    flagged += f.bets.length;
    rows.push(`<tr class="${f.bets.length ? "flag" : ""}"><td class="l">${esc(f.date)}</td><td class="l">${esc(f.league)}</td>
      <td class="l"><a href="#" data-match='${esc(JSON.stringify([f.league, f.home, f.away]))}'>${esc(f.home)} v ${esc(f.away)}</a>${f.known ? "" : ' <span class="pill mid">new team</span>'}</td>
      <td>${pct(f.p_home, 0)}</td><td>${pct(f.p_draw, 0)}</td><td>${pct(f.p_away, 0)}</td><td>${num(f.xg_home, 2)}–${num(f.xg_away, 2)}</td><td>${pct(f.p_over25, 0)}</td>
      <td class="l">${bets || '<span class="muted">no price edge</span>'}</td></tr>`);
  }
  $("#board").innerHTML = `<p class="small muted">${b.fixtures.length} fixtures, ${flagged} flagged prices. A flag is a price, not a prediction: the team can still lose.</p>
    <div class="scroll"><table><thead><tr><th class="l">Date</th><th class="l">Lg</th><th class="l">Match</th><th>Home</th><th>Draw</th><th>Away</th><th>Exp. goals</th><th>Over 2.5</th><th class="l">Flagged</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table></div>`;
  $$("[data-match]", $("#board")).forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    const [lg, h, aw] = JSON.parse(a.dataset.match);
    showTab("football");
    setLeague(lg).then(() => { $("#f-home").value = h; $("#f-away").value = aw; analyseFootball(); });
  }));
}
$("#refresh-board").addEventListener("click", loadBoard);

// --------------------------------------------------------------- football
let LEAGUES = [];
async function loadLeagues() {
  LEAGUES = await api("/api/football/leagues");
  $("#f-league").innerHTML = LEAGUES.map((l) => `<option value="${esc(l.code)}">${esc(l.name)} (${esc(l.season)})</option>`).join("");
  if (LEAGUES.length) await setLeague(load("league") || LEAGUES[0].code);
}

async function setLeague(code) {
  const lg = LEAGUES.find((l) => l.code === code) || LEAGUES[0];
  $("#f-league").value = lg.code;
  store("league", lg.code);
  const opts = lg.teams.map((t) => `<option>${esc(t)}</option>`).join("");
  $("#f-home").innerHTML = opts;
  $("#f-away").innerHTML = opts;
  $("#f-away").selectedIndex = Math.min(1, lg.teams.length - 1);
  const [ratings, table] = await Promise.all([api("/api/football/ratings/" + lg.code), api("/api/football/table/" + lg.code)]);
  $("#f-ratings").innerHTML = `<table><thead><tr><th class="l">#</th><th class="l">Team</th><th>Goals for</th><th>Goals against</th><th>Net</th></tr></thead><tbody>
    ${ratings.map((r) => `<tr><td class="l">${r.rank}</td><td class="l">${esc(r.team)}</td><td>${num(r.exp_gf)}</td><td>${num(r.exp_ga)}</td><td class="${cls(r.strength)}">${num(r.strength)}</td></tr>`).join("")}</tbody></table>`;
  $("#f-table").innerHTML = `<table><thead><tr><th class="l">#</th><th class="l">Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr></thead><tbody>
    ${table.map((t) => `<tr><td class="l">${t.pos}</td><td class="l">${esc(t.team)}</td><td>${t.p}</td><td>${t.w}</td><td>${t.d}</td><td>${t.l}</td><td class="${cls(t.gd)}">${t.gd}</td><td><b>${t.pts}</b></td></tr>`).join("")}</tbody></table>`;
}
$("#f-league").addEventListener("change", (e) => setLeague(e.target.value));

async function analyseFootball() {
  const odds = {};
  $$("[data-odds]").forEach((i) => { if (i.value) odds[i.dataset.odds] = +i.value; });
  const body = { league: $("#f-league").value, home: $("#f-home").value, away: $("#f-away").value, neutral: $("#f-neutral").checked, odds, ...staking() };
  $("#f-result").innerHTML = '<div class="card muted">Fitting model…</div>';
  let r;
  try { r = await api("/api/football/match", body); } catch (e) { $("#f-result").innerHTML = `<div class="card neg">${esc(e.message)}</div>`; return; }
  $("#f-result").innerHTML = renderFootball(r);
  const m = r.home_report, a = r.away_report;
  chart("f-goals", {
    type: "bar",
    data: {
      labels: ["Goals/game", "Conceded/game", "Shots on target", "SoT against"],
      datasets: [
        { label: r.home, data: [m.season_stats.gf_pg, m.season_stats.ga_pg, m.season_stats.sot_pg, m.season_stats.sot_against_pg], backgroundColor: "#3ddc97" },
        { label: r.away, data: [a.season_stats.gf_pg, a.season_stats.ga_pg, a.season_stats.sot_pg, a.season_stats.sot_against_pg], backgroundColor: "#4ea8de" },
      ],
    },
    options: { maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } },
  });
}
$("#f-go").addEventListener("click", analyseFootball);

function renderFootball(r) {
  const p = r.probs;
  const warn = [];
  if (!r.known.home || !r.known.away) warn.push("One team has little recent data, so its rating is mostly a prior. Treat with extra caution.");
  const maxCell = Math.max(...r.matrix.flat());
  const heat = r.matrix.map((row, i) => `<tr><th>${i}</th>${row.map((v) => `<td style="background:rgba(61,220,151,${(0.85 * v) / maxCell})">${(100 * v).toFixed(1)}</td>`).join("")}</tr>`).join("");
  const val = r.value.map((v) => {
    const flag = !v.value ? "" : v.basis === "price" ? '<span class="pill good">price edge</span>' : `<span class="pill mid">${v.basis} only · unproven</span>`;
    return `<tr class="${v.value ? "flag" : ""}"><td class="l">${esc(v.label)}</td><td>${pct(v.p_model)}</td><td>${num(v.fair_odds)}</td><td>${pct(v.p_market)}</td>
      <td>${num(v.odds)}</td><td class="${cls(v.price_edge)}">${v.market_source === "consensus" ? spct(v.price_edge) : "–"}</td><td class="${cls(v.model_edge)}">${spct(v.model_edge)}</td>
      <td>${v.stake ? money(v.stake) : "–"}</td><td>${flag}</td></tr>`;
  }).join("");
  const sideStats = (t) => {
    const s = t.season_stats;
    return `<h3>${esc(t.team)} ${formHtml(t.form)}</h3>
    <table><tbody>
      <tr><td class="l">Season ${esc(t.season || "")} record</td><td>${s.w ?? 0}-${s.d ?? 0}-${s.l ?? 0} (${num(s.ppg)} pts/game)</td></tr>
      <tr><td class="l">Home / away pts per game</td><td>${num(t.home_stats.ppg)} / ${num(t.away_stats.ppg)}</td></tr>
      <tr><td class="l">Scored / conceded per game</td><td>${num(s.gf_pg)} / ${num(s.ga_pg)}</td></tr>
      <tr><td class="l">Shots on target for / against</td><td>${num(s.sot_pg, 1)} / ${num(s.sot_against_pg, 1)}</td></tr>
      <tr><td class="l">Finishing (goals per SoT)</td><td>${pct(s.conversion, 0)} ${s.conversion > 0.4 ? '<span class="pill mid">likely to cool</span>' : s.conversion < 0.22 ? '<span class="pill mid">likely to improve</span>' : ""}</td></tr>
      <tr><td class="l">Clean sheets · BTTS · Over 2.5</td><td>${pct(s.clean_sheets, 0)} · ${pct(s.btts, 0)} · ${pct(s.over25, 0)}</td></tr>
      <tr><td class="l">Last 10: pts per game</td><td>${num(t.recent_stats.ppg)}</td></tr>
    </tbody></table>
    <p class="small muted">${t.recent.slice(0, 6).map((x) => `${esc(x.date)} ${esc(x.venue)} ${esc(x.opponent)} ${esc(x.score)}`).join("<br>")}</p>`;
  };
  const h = r.h2h;
  return `
  <div class="card">
    <div class="row-between"><h2>${esc(r.home)} v ${esc(r.away)}</h2><span class="muted small">Model fitted on ${r.model.fitted_on} matches to ${esc(r.model.as_of)} · home advantage ×${num(r.model.home_advantage)}</span></div>
    ${warn.map((w) => `<p class="pill mid">${esc(w)}</p>`).join("")}
    <div class="probbar"><div class="h" style="width:${100 * p.home}%">${pct(p.home, 0)}</div><div class="d" style="width:${100 * p.draw}%">${pct(p.draw, 0)}</div><div class="a" style="width:${100 * p.away}%">${pct(p.away, 0)}</div></div>
    <div class="row-between small muted"><span>${esc(r.home)} win</span><span>Draw</span><span>${esc(r.away)} win</span></div>
    <div class="grid3" style="margin-top:14px">
      <div class="kpi"><div class="k">Expected goals</div><div class="v">${num(p.xg_home)} – ${num(p.xg_away)}</div></div>
      <div class="kpi"><div class="k">Over 2.5 goals</div><div class="v">${pct(p["over_2.5"], 0)}</div></div>
      <div class="kpi"><div class="k">Both teams score</div><div class="v">${pct(p.btts_yes, 0)}</div></div>
      <div class="kpi"><div class="k">Most likely score</div><div class="v">${esc(r.top_scores[0].score)} <span class="small muted">${pct(r.top_scores[0].p)}</span></div></div>
    </div>
  </div>
  <div class="card">
    <h3>Prices and value</h3>
    <div class="scroll"><table><thead><tr><th class="l">Selection</th><th>Model</th><th>Fair odds</th><th>Market</th><th>Odds</th><th>Price edge</th><th>Model edge</th><th>Stake</th><th></th></tr></thead><tbody>${val}</tbody></table></div>
    <p class="small muted">"Market" is the bookmakers' own probability with the margin removed; it needs a full set of odds. "Price edge" compares your price with that.
      "Model edge" compares it with our model, which lost money on its own in testing, so it is shown for information only.
      ${r.consensus && r.consensus.home ? "Consensus odds came from the fixtures feed." : "No market-average odds for this match, so there is no price edge to measure. With your bookmaker's full set of prices, flags fall back to a 50/50 model and market blend, which lost money in testing: treat those as opinions, not value."}</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Score probabilities (%) <span class="muted small">rows: ${esc(r.home)} goals, columns: ${esc(r.away)}</span></h3>
      <div class="scroll"><table class="heat"><thead><tr><th></th>${r.matrix[0].map((_, j) => `<th>${j}</th>`).join("")}</tr></thead><tbody>${heat}</tbody></table></div>
      <p class="small muted">Top scores: ${r.top_scores.slice(0, 5).map((s) => `${esc(s.score)} (${pct(s.p)})`).join(", ")}</p></div>
    <div class="card"><h3>This season compared</h3><div class="chartbox"><canvas id="f-goals"></canvas></div></div>
  </div>
  <div class="grid2">
    <div class="card">${sideStats(r.home_report)}</div>
    <div class="card">${sideStats(r.away_report)}</div>
  </div>
  <div class="card"><h3>Head to head (last ${h.played})</h3>
    <p>${esc(r.home)} ${h.a_wins} · draws ${h.draws} · ${esc(r.away)} ${h.b_wins} · avg goals ${num(h.avg_goals, 1)}</p>
    <p class="small muted">${h.matches.map((m) => `${esc(m.date)}: ${esc(m.home)} ${esc(m.score)} ${esc(m.away)}`).join("<br>")}</p>
    <p class="small muted">Head-to-head records are small samples and add little beyond current team strength; the model doesn't use them.</p></div>`;
}

// ----------------------------------------------------------------- tennis
async function loadTennis() {
  let top;
  try { top = await api("/api/tennis/players?limit=40"); } catch (e) { $("#t-top").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  $("#t-top").innerHTML = `<table><thead><tr><th class="l">#</th><th class="l">Player</th><th>Elo</th><th>Hard</th><th>Clay</th><th>Grass</th><th>Matches</th><th>Last match</th></tr></thead><tbody>
    ${top.map((p, i) => `<tr><td class="l">${i + 1}</td><td class="l">${esc(p.player)}</td><td><b>${Math.round(p.elo)}</b></td><td>${Math.round(p.elo_hard)}</td><td>${Math.round(p.elo_clay)}</td><td>${Math.round(p.elo_grass)}</td><td>${p.matches}</td><td>${esc(p.last_played)}</td></tr>`).join("")}</tbody></table>`;
  $("#t-players").innerHTML = top.map((p) => `<option value="${esc(p.player)}">`).join("");
  if (top.length > 1) { $("#t-p1").value ||= top[0].player; $("#t-p2").value ||= top[1].player; }
}
let tTimer;
["#t-p1", "#t-p2"].forEach((s) => $(s).addEventListener("input", (e) => {
  clearTimeout(tTimer);
  tTimer = setTimeout(async () => {
    const q = e.target.value.trim();
    if (q.length < 2) return;
    const res = await api("/api/tennis/players?limit=20&q=" + encodeURIComponent(q));
    $("#t-players").innerHTML = res.map((p) => `<option value="${esc(p.player)}">`).join("");
  }, 200);
}));

async function analyseTennis() {
  const body = { p1: $("#t-p1").value.trim(), p2: $("#t-p2").value.trim(), surface: $("#t-surface").value, best_of: +$("#t-bo").value,
    odds1: +$("#t-o1").value || null, odds2: +$("#t-o2").value || null, ...staking() };
  let r;
  try { r = await api("/api/tennis/match", body); } catch (e) { $("#t-result").innerHTML = `<div class="card neg">${esc(e.message)}</div>`; return; }
  const pl = r.players;
  const lowData = pl.filter((p) => p.matches < 20).map((p) => p.name);
  const val = (r.value || []).map((v) => `<tr class="${v.value ? "flag" : ""}"><td class="l">${esc(v.player)}</td><td>${num(v.odds)}</td><td>${pct(v.p_model)}</td><td>${pct(v.p_market)}</td><td>${pct(v.p_blend)}</td>
    <td class="${cls(v.blend_edge)}">${spct(v.blend_edge)}</td><td>${v.stake ? money(v.stake) : "–"}</td><td>${v.value ? '<span class="pill good">flag</span>' : ""}</td></tr>`).join("");
  const side = (p) => `<h3>${esc(p.name)}</h3><table><tbody>
    <tr><td class="l">Overall Elo</td><td>${Math.round(p.elo)}</td></tr><tr><td class="l">${esc(r.surface)} Elo</td><td>${Math.round(p.surface_elo)} (${p.surface_matches} matches)</td></tr>
    <tr><td class="l">Rating used</td><td><b>${Math.round(p.blended)}</b></td></tr></tbody></table>
    <p class="small muted">${p.recent.map((m) => `${esc(m.date)} ${m.result === "W" ? '<span class="pos">W</span>' : '<span class="neg">L</span>'} v ${esc(m.opponent)} (${esc(m.tournament)}, ${esc(m.surface)})`).join("<br>")}</p>`;
  $("#t-result").innerHTML = `
  <div class="card">
    <h2>${esc(r.p1)} v ${esc(r.p2)} <span class="muted small">${esc(r.surface)}, best of ${r.best_of}</span></h2>
    ${lowData.length ? `<p class="pill mid">Few rated matches for ${lowData.map(esc).join(", ")}: rating is unreliable.</p>` : ""}
    <div class="probbar"><div class="h" style="width:${100 * r.p1_win}%">${pct(r.p1_win, 0)}</div><div class="a" style="width:${100 * r.p2_win}%">${pct(r.p2_win, 0)}</div></div>
    <p class="small muted">Fair odds: ${esc(r.p1)} ${num(r.fair_odds[0])} · ${esc(r.p2)} ${num(r.fair_odds[1])} · Head to head ${r.h2h.a_wins}-${r.h2h.b_wins}</p>
    ${r.value ? `<div class="scroll"><table><thead><tr><th class="l">Player</th><th>Odds</th><th>Model</th><th>Market</th><th>Blend</th><th>Edge</th><th>Stake</th><th></th></tr></thead><tbody>${val}</tbody></table></div>
      <p class="small muted">Bookmaker margin on these prices: ${pct(r.overround)}. The edge uses a 50/50 blend of model and market, because the market is usually better informed (injuries, fatigue, motivation).</p>` : '<p class="small muted">Enter both odds to see edge and stake.</p>'}
  </div>
  <div class="grid2"><div class="card">${side(pl[0])}</div><div class="card">${side(pl[1])}</div></div>
  <div class="card"><h3>Elo history</h3><div class="chartbox"><canvas id="t-hist"></canvas></div></div>`;
  chart("t-hist", {
    type: "line",
    data: { datasets: pl.map((p, i) => ({ label: p.name, data: p.history.map(([d, v]) => ({ x: d.slice(0, 10), y: v })), borderColor: i ? "#4ea8de" : "#3ddc97", pointRadius: 0, tension: 0.2 })) },
    options: { maintainAspectRatio: false, parsing: false, scales: { x: { type: "category", labels: [...new Set(pl.flatMap((p) => p.history.map(([d]) => d.slice(0, 10))))].sort(), ticks: { maxTicksLimit: 8 } } }, plugins: { legend: { position: "bottom" } } },
  });
}
$("#t-go").addEventListener("click", analyseTennis);

// --------------------------------------------------------------- backtest
let btLoaded = false;
async function loadBacktests() {
  if (btLoaded) return;
  const bt = await api("/api/backtests");
  btLoaded = true;
  const out = [];
  if (!bt.football && !bt.tennis) {
    $("#bt").innerHTML = '<div class="card muted">No backtest yet. Run <code>python -m betit backtest</code>.</div>';
    return;
  }
  if (bt.football) out.push(renderFootballBt(bt.football));
  if (bt.tennis) out.push(renderTennisBt(bt.tennis));
  $("#bt").innerHTML = out.join("");
  if (bt.football) {
    const colors = ["#ef6461", "#f2b134", "#3ddc97", "#4ea8de"];
    chart("bt-equity", {
      type: "line",
      data: { datasets: Object.values(bt.football.strategies).filter((s) => s.bets).map((s, i) => ({ label: s.label, data: s.equity.map((e) => ({ x: e.date, y: e.bankroll })), borderColor: colors[i], pointRadius: 0, borderWidth: 2 })) },
      options: { maintainAspectRatio: false, parsing: false, scales: { x: { type: "category", labels: [...new Set(Object.values(bt.football.strategies).flatMap((s) => (s.equity || []).map((e) => e.date)))].sort(), ticks: { maxTicksLimit: 8 } }, y: { title: { display: true, text: "Bankroll (start 1,000)" } } }, plugins: { legend: { position: "bottom" } } },
    });
    calibChart("bt-calib", bt.football.accuracy.calibration);
  }
  if (bt.tennis) calibChart("bt-tcalib", bt.tennis.accuracy.calibration);
}

function calibChart(id, cal) {
  chart(id, {
    type: "scatter",
    data: { datasets: [
      { label: "Model", data: cal.map((c) => ({ x: c.predicted, y: c.observed })), backgroundColor: "#3ddc97", pointRadius: 5 },
      { label: "Perfect calibration", type: "line", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: "#8b98a5", borderDash: [4, 4], pointRadius: 0 },
    ] },
    options: { maintainAspectRatio: false, scales: { x: { min: 0, max: 1, title: { display: true, text: "Predicted probability" } }, y: { min: 0, max: 1, title: { display: true, text: "How often it happened" } } }, plugins: { legend: { position: "bottom" } } },
  });
}

function strategyRows(strats) {
  return Object.values(strats).map((s) => s.bets ? `<tr><td class="l"><b>${esc(s.label)}</b><br><span class="small muted">${esc(s.description)}</span></td>
    <td>${s.bets.toLocaleString()}</td><td>${pct(s.win_rate)}</td><td>${num(s.avg_odds)}</td><td class="${cls(s.flat_roi)}"><b>${spct(s.flat_roi)}</b></td>
    <td class="small">${spct(s.flat_roi_ci95[0])} to ${spct(s.flat_roi_ci95[1])}</td><td>${money(s.kelly_end)}</td><td class="neg">${pct(s.max_drawdown, 0)}</td><td>${s.longest_losing_streak}</td></tr>`
    : `<tr><td class="l">${esc(s.label)}</td><td colspan="8" class="muted">no bets</td></tr>`).join("");
}

function renderFootballBt(f) {
  const a = f.accuracy;
  const pl = f.per_league.map((l) => `<tr><td class="l">${esc(l.name)}</td><td>${l.matches}</td><td>${num(l.model_logloss, 4)}</td><td>${num(l.market_logloss, 4)}</td><td>${pct(l.model_hit_rate)}</td><td>${pct(l.market_hit_rate)}</td></tr>`).join("");
  return `
  <div class="card truth">
    <h2>Football: walk-forward backtest, ${esc(f.start)} to ${esc(f.end)}</h2>
    <p>Every prediction was made using only matches played before it, refitting weekly, across ${f.leagues.length} leagues and ${a.matches.toLocaleString()} matches.
      Bets were settled at the odds that were actually on offer.</p>
    <div class="grid3">
      <div class="kpi"><div class="k">Model log loss (lower is better)</div><div class="v">${num(a.model_logloss, 4)}</div></div>
      <div class="kpi"><div class="k">Bookmaker log loss</div><div class="v">${num(a.market_logloss, 4)}</div></div>
      <div class="kpi"><div class="k">Favourite won: model / market</div><div class="v">${pct(a.model_hit_rate, 0)} / ${pct(a.market_hit_rate, 0)}</div></div>
    </div>
    <p><b>Verdict:</b> the bookmakers' prices were ${a.market_logloss < a.model_logloss ? "more accurate than" : "no better than"} our model.
      Where the model disagreed with the market, the market was usually right, so "value bets" picked by the model alone lost money.</p>
  </div>
  <div class="card">
    <h3>Strategies (decided before looking at results)</h3>
    <div class="scroll"><table><thead><tr><th class="l">Strategy</th><th>Bets</th><th>Won</th><th>Avg odds</th><th>ROI (flat)</th><th>ROI 95% range</th><th>Kelly: 1,000 →</th><th>Max drawdown</th><th>Worst losing run</th></tr></thead>
    <tbody>${strategyRows(f.strategies)}</tbody></table></div>
    <p class="small muted">The 95% range shows how much of the ROI could be luck. If it includes 0%, the result is not proven.
      "Best available odds" in historical data includes prices that were stale or limited to tiny stakes, so line-shopping results are optimistic.</p>
  </div>
  <div class="grid2">
    <div class="card"><h3>Bankroll over time (fractional Kelly)</h3><div class="chartbox"><canvas id="bt-equity"></canvas></div></div>
    <div class="card"><h3>Calibration: are the probabilities honest?</h3><div class="chartbox"><canvas id="bt-calib"></canvas></div></div>
  </div>
  <div class="card"><h3>By league</h3><div class="scroll"><table><thead><tr><th class="l">League</th><th>Matches</th><th>Model log loss</th><th>Market log loss</th><th>Model hit</th><th>Market hit</th></tr></thead><tbody>${pl}</tbody></table></div></div>`;
}

function renderTennisBt(t) {
  const a = t.accuracy;
  const yrs = t.per_year.map((y) => `<tr><td class="l">${esc(y.tour)} ${y.year}</td><td>${y.matches}</td><td>${pct(y.model_hit_rate)}</td><td>${num(y.model_logloss, 4)}</td><td>${y.market_logloss ? num(y.market_logloss, 4) : "–"}</td></tr>`).join("");
  return `
  <div class="card truth">
    <h2>Tennis: surface Elo, ${esc(t.start)} to ${esc(t.end)}</h2>
    <div class="grid3">
      <div class="kpi"><div class="k">Matches predicted</div><div class="v">${a.matches.toLocaleString()}</div></div>
      <div class="kpi"><div class="k">Favourite won</div><div class="v">${pct(a.model_hit_rate)}</div></div>
      <div class="kpi"><div class="k">Log loss</div><div class="v">${num(a.model_logloss, 4)}</div></div>
    </div>
    ${t.strategies ? `<h3 style="margin-top:16px">Betting strategies</h3><div class="scroll"><table><thead><tr><th class="l">Strategy</th><th>Bets</th><th>Won</th><th>Avg odds</th><th>ROI (flat)</th><th>ROI 95% range</th><th>Kelly: 1,000 →</th><th>Max drawdown</th><th>Worst losing run</th></tr></thead><tbody>${strategyRows(t.strategies)}</tbody></table></div>`
      : `<p class="muted">The loaded tennis data has no bookmaker odds, so only accuracy is shown. Run <code>python -m betit update</code> on a machine that can reach tennis-data.co.uk to add odds and a betting backtest.</p>`}
  </div>
  <div class="grid2">
    <div class="card"><h3>Calibration</h3><div class="chartbox"><canvas id="bt-tcalib"></canvas></div></div>
    <div class="card"><h3>By year</h3><div class="scroll"><table><thead><tr><th class="l">Year</th><th>Matches</th><th>Hit rate</th><th>Log loss</th><th>Market</th></tr></thead><tbody>${yrs}</tbody></table></div></div>
  </div>`;
}

// --------------------------------------------------------- bankroll tools
function kellyCalc() {
  const p = +$("#k-p").value / 100, o = +$("#k-o").value;
  const b = o - 1, full = Math.max(0, (p * b - (1 - p)) / b);
  const ev = p * o - 1;
  const bank = staking().bankroll;
  $("#k-out").innerHTML = `<div class="grid3">
    <div class="kpi"><div class="k">Expected value per 1 staked</div><div class="v ${cls(ev)}">${spct(ev)}</div></div>
    <div class="kpi"><div class="k">Full Kelly</div><div class="v">${pct(full)} · ${money(full * bank)}</div></div>
    <div class="kpi"><div class="k">Your setting (${pct(+$("#kelly").value, 0)} Kelly, capped)</div><div class="v">${money(bank * Math.min(+$("#cap").value, full * +$("#kelly").value))}</div></div></div>
    ${ev <= 0 ? '<p class="neg">No edge at this price: the right stake is zero.</p>' : ""}`;
}
function goalCalc() {
  const target = +$("#g-target").value, roi = +$("#g-roi").value / 100;
  if (roi <= 0) { $("#g-out").innerHTML = '<p class="neg">With no edge, expected profit is negative whatever you stake.</p>'; return; }
  const turnover = target / roi;
  const bankroll = turnover / 0.02 / 10; // ~10 bets a day at 2% of bankroll each
  $("#g-out").innerHTML = `<div class="grid3">
    <div class="kpi"><div class="k">Must stake every day</div><div class="v">$${money(Math.round(turnover))}</div></div>
    <div class="kpi"><div class="k">Bankroll needed (10 bets/day at 2%)</div><div class="v">$${money(Math.round(bankroll))}</div></div>
    <div class="kpi"><div class="k">Typical swing in one day (±1 sd)</div><div class="v">$${money(Math.round(turnover / 10 * Math.sqrt(10) * 1.0))}</div></div></div>
    <p class="small muted">That is an expected average, before bookmakers limit or close winning accounts, which they do quickly at this size.
    Even the best professional syndicates work on edges of 1-3% with private data and access to high-limit exchanges.</p>`;
}
["#k-p", "#k-o"].forEach((s) => $(s).addEventListener("input", kellyCalc));
["#g-target", "#g-roi"].forEach((s) => $(s).addEventListener("input", goalCalc));

// ------------------------------------------------------------------- init
(async function init() {
  kellyCalc();
  goalCalc();
  try { await loadStatus(); } catch (e) { $("#status").textContent = "API unavailable"; }
  loadBoard();
  loadLeagues().catch((e) => ($("#f-ratings").innerHTML = `<p class="muted">${esc(e.message)}</p>`));
  loadTennis();
  const t = load("tab");
  if (t) showTab(t);
})();
