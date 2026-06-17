"""Generate a self-contained benchmarks.html from benchmarks.json.

Source of truth is benchmarks.json (easy to append to by hand or from a script).
This regenerates the HTML so the page works offline from file:// with no fetch.

Usage:  python -m tools.bench_report
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks.json"
OUT = ROOT / "benchmarks.html"

COLUMNS = [
    ("timestamp", "Time", "text"),
    ("environment", "Environment", "text"),
    ("accel", "Accel", "text"),
    ("stt_config", "STT Config", "text"),
    ("stt_ms", "STT (ms)", "ms"),
    ("llm_model", "LLM Model", "text"),
    ("llm_quant", "Quant", "text"),
    ("llm_first_token_ms", "LLM 1st tok (ms)", "ms"),
    ("llm_ms", "LLM (ms)", "ms"),
    ("tts_config", "TTS Config", "text"),
    ("tts_ms", "TTS (ms)", "ms"),
    ("first_audio_ms", "1st audio (ms)", "ms"),
    ("total_ms", "TOTAL (ms)", "ms"),
    ("notes", "Notes", "text"),
    ("coldstart_ms", "Coldstart (ms)", "ms"),
    ("prompt_text", "Prompt", "hidden"),
]

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>robot — latency benchmarks</title>
<style>
  :root {
    --bg:#0f1419; --panel:#1a2129; --line:#2a333d; --fg:#e6edf3; --mut:#8b97a3;
    --good:#3fb950; --bad:#f85149; --accent:#58a6ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { padding:20px 24px; border-bottom:1px solid var(--line); }
  h1 { margin:0 0 4px; font-size:20px; }
  .sub { color:var(--mut); font-size:13px; }
  .bar { display:flex; gap:12px; align-items:center; flex-wrap:wrap;
    padding:14px 24px; border-bottom:1px solid var(--line); }
  .bar label { color:var(--mut); font-size:13px; }
  select, input { background:var(--panel); color:var(--fg); border:1px solid var(--line);
    border-radius:6px; padding:6px 10px; font-size:13px; }
  .cards { display:flex; gap:12px; flex-wrap:wrap; padding:16px 24px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:12px 16px; min-width:180px; }
  .card .env { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .card .val { font-size:22px; font-weight:600; margin-top:4px; }
  .card .val small { font-size:13px; color:var(--mut); font-weight:400; }
  .wrap { padding:0 24px 40px; overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
  th, td { padding:9px 12px; border-bottom:1px solid var(--line); text-align:left;
    white-space:nowrap; }
  th { position:sticky; top:0; background:var(--bg); cursor:pointer; user-select:none;
    color:var(--mut); font-weight:600; }
  th:hover { color:var(--fg); }
  th .arrow { opacity:.5; font-size:11px; }
  td.ms { text-align:right; }
  tr:hover td { background:#161d24; }
  .best { color:var(--good); font-weight:700; }
  .worst { color:var(--bad); }
  td.notes { white-space:normal; color:var(--mut); min-width:220px; font-size:13px; }
  .total { font-weight:700; }
  th.prompt-th { width:28px; min-width:28px; text-align:center; color:var(--mut); font-size:15px; cursor:default; }
  th.prompt-th:hover { color:var(--fg); }
  td.prompt-cell { width:28px; min-width:28px; text-align:center; position:relative; cursor:default; }
  td.prompt-cell .prompt-tip {
    visibility:hidden; opacity:0; transition:opacity .15s;
    position:absolute; right:0; top:100%; margin-top:4px;
    background:var(--panel); border:1px solid var(--line); border-radius:8px;
    padding:10px 14px; width:340px; white-space:normal; font-size:12px;
    color:var(--fg); z-index:50; box-shadow:0 6px 20px rgba(0,0,0,.5);
    text-align:left; line-height:1.5;
  }
  td.prompt-cell:hover .prompt-tip { visibility:visible; opacity:1; }
  footer { color:var(--mut); font-size:12px; padding:0 24px 30px; }
  code { background:var(--panel); padding:2px 6px; border-radius:4px; }
</style>
</head>
<body>
<header>
  <h1>robot — voice pipeline latency benchmarks</h1>
  <div class="sub">mic → STT → LLM → TTS. Lower is better. Source of truth:
    <code>benchmarks.json</code> → regenerate with <code>python -m tools.bench_report</code>.</div>
</header>

<div class="bar">
  <label for="env">Environment</label>
  <select id="env"><option value="">All</option></select>
  <label><input type="checkbox" id="warm"> Hide cold-start runs</label>
  <span class="sub" id="count"></span>
</div>

<div class="cards" id="cards"></div>

<div class="wrap">
  <table id="tbl"><thead></thead><tbody></tbody></table>
</div>

<footer>
  Green = fastest in column · red = slowest. Click a header to sort. TOTAL is real
  end-to-end (STT + LLM + TTS); LLM 1st-token is informational (subset of LLM).
</footer>

<script>
const COLUMNS = __COLUMNS__;
const DATA = __DATA__;
let sortKey = "total_ms", sortDir = 1;

const msKeys = COLUMNS.filter(c => c[2] === "ms").map(c => c[0]);
const envSel = document.getElementById("env");
const warmChk = document.getElementById("warm");

[...new Set(DATA.records.map(r => r.environment))].sort().forEach(e => {
  const o = document.createElement("option"); o.value = e; o.textContent = e; envSel.appendChild(o);
});

function isCold(r){ return /cold/i.test(r.notes || ""); }

function filtered(){
  let rows = DATA.records.slice();
  if (envSel.value) rows = rows.filter(r => r.environment === envSel.value);
  if (warmChk.checked) rows = rows.filter(r => !isCold(r));
  rows.sort((a,b) => {
    const x=a[sortKey], y=b[sortKey];
    if (typeof x === "number" && typeof y === "number") return (x-y)*sortDir;
    return String(x).localeCompare(String(y))*sortDir;
  });
  return rows;
}

function bestWorst(rows){
  const ext = {};
  msKeys.forEach(k => {
    const vals = rows.map(r => r[k]).filter(v => typeof v === "number");
    if (vals.length) ext[k] = { min: Math.min(...vals), max: Math.max(...vals) };
  });
  return ext;
}

function renderHead(){
  const tr = document.createElement("tr");
  COLUMNS.forEach(([key,label,type]) => {
    const th = document.createElement("th");
    if (type === "hidden") {
      th.className = "prompt-th";
      th.textContent = "💬";
      th.title = "Prompt text (hover a row cell to see)";
      th.onclick = null;
    } else {
      const arrow = key===sortKey ? (sortDir>0?" \u25B2":" \u25BC") : "";
      th.innerHTML = label + '<span class="arrow">'+arrow+'</span>';
      th.onclick = () => { if(sortKey===key) sortDir*=-1; else {sortKey=key; sortDir=1;} render(); };
    }
    tr.appendChild(th);
  });
  const thead = document.querySelector("#tbl thead"); thead.innerHTML=""; thead.appendChild(tr);
}

function renderBody(rows, ext){
  const tb = document.querySelector("#tbl tbody"); tb.innerHTML="";
  rows.forEach(r => {
    const tr = document.createElement("tr");
    COLUMNS.forEach(([key,label,type]) => {
      const td = document.createElement("td");
      const v = r[key];
      if (type === "ms"){
        td.className = "ms" + (key==="total_ms" ? " total":"");
        td.textContent = (typeof v==="number") ? v.toLocaleString() : "—";
        if (ext[key] && typeof v==="number" && rows.length>1){
          if (v===ext[key].min) td.classList.add("best");
          else if (v===ext[key].max) td.classList.add("worst");
        }
      } else if (key === "notes"){
        td.className = "notes"; td.textContent = v || "";
      } else if (type === "hidden"){
        td.className = "prompt-cell";
        if (v) {
          const esc = v.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
          td.innerHTML = '&#x1F4AC;<span class="prompt-tip">'+esc+'</span>';
        } else {
          td.innerHTML = '<span style="color:var(--line)">&#x2014;</span>';
        }
      } else if (key === "timestamp") {
        td.textContent = (v ?? "").replace("T", " ");
      } else {
        td.textContent = v ?? "";
      }
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
}

function renderCards(){
  const cards = document.getElementById("cards"); cards.innerHTML="";
  const envs = [...new Set(DATA.records.map(r => r.environment))].sort();
  envs.forEach(env => {
    const warm = DATA.records.filter(r => r.environment===env && !isCold(r));
    const pool = warm.length ? warm : DATA.records.filter(r=>r.environment===env);
    const best = Math.min(...pool.map(r => r.total_ms));
    const c = document.createElement("div"); c.className="card";
    c.innerHTML = '<div class="env">'+env+'</div><div class="val">'+
      best.toLocaleString()+' <small>ms best total</small></div>';
    cards.appendChild(c);
  });
}

function render(){
  const rows = filtered();
  const ext = bestWorst(rows);
  renderHead(); renderBody(rows, ext); renderCards();
  document.getElementById("count").textContent = rows.length + " runs shown";
}

envSel.onchange = render; warmChk.onchange = render;
render();
</script>
</body>
</html>
"""


def main() -> int:
    data = json.loads(DATA.read_text())
    html = (
        TEMPLATE
        .replace("__COLUMNS__", json.dumps(COLUMNS))
        .replace("__DATA__", json.dumps(data))
    )
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)} from {len(data['records'])} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
