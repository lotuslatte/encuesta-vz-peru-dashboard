#!/usr/bin/env python3
"""Genera index.html (dashboard agregado) desde la API de KoboToolbox.

- Solo librería estándar (corre tal cual en el runner de GitHub Actions).
- El token se lee del entorno KOBO_TOKEN (secret en Actions; export local).
- FILTRA la PII y emite ÚNICAMENTE agregados/conteos: ninguna fila individual
  ni dato identificable llega al HTML.

Uso local:
    KOBO_TOKEN=xxxx python3 build_dashboard.py
"""
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

KOBO_DOMAIN = 'kf.kobotoolbox.org'
ASSET_UID = 'abz52mVkVXjhDRkSkj4fHJ'
HERE = Path(__file__).resolve().parent
LABELS = json.loads((HERE / 'labels.json').read_text(encoding='utf-8'))
FIELD2LIST = LABELS['field2list']
LISTS = LABELS['lists']

# --- Filtro de PII (idéntico criterio que kobo_to_sheets.gs) ---------------
PII_LEAF = {'respondent_first_name', 'respondent_last_name', 'ruc',
            'recontact', 'deviceid'}
META_KEEP = {'_submission_time', '_id'}


def leaf(col):
    return col.rsplit('/', 1)[-1]


def excluded(col):
    lf = leaf(col)
    if lf in PII_LEAF:
        return True
    if lf.endswith('_other'):
        return True
    if 'gps' in lf.lower():
        return True
    if col.startswith('meta/') or col.startswith('formhub/'):
        return True
    if col in ('__version__', '_xform_id_string', '_status'):
        return True
    if col.startswith('_') and col not in META_KEEP:
        return True
    return False


def label(field, code):
    """Etiqueta legible para (campo, código); cae al código si no hay mapa."""
    code = str(code)
    lst = FIELD2LIST.get(field)
    if lst and code in LISTS.get(lst, {}):
        return LISTS[lst][code]
    return code


# --- Descarga paginada -----------------------------------------------------
def fetch_all(token):
    base = f'https://{KOBO_DOMAIN}/api/v2/assets/{ASSET_UID}/data.json'
    out, start, limit = [], 0, 30000
    while True:
        url = f'{base}?format=json&limit={limit}&start={start}'
        req = urllib.request.Request(url, headers={
            'Authorization': f'Token {token}', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode('utf-8'))
        results = body.get('results', body if isinstance(body, list) else [])
        out.extend(results)
        if not body.get('next') or not results:
            break
        start += len(results)
    return out


# --- Agregaciones ----------------------------------------------------------
def to_leaf_rows(submissions):
    """Convierte cada submission a dict {leaf: valor}, excluyendo PII."""
    rows = []
    for s in submissions:
        row = {}
        for k, v in s.items():
            if excluded(k):
                continue
            row[leaf(k)] = v
        rows.append(row)
    return rows


def counts(rows, field, top=None, as_label=True):
    c = Counter()
    for r in rows:
        v = r.get(field)
        if v in (None, ''):
            continue
        c[label(field, v) if as_label else str(v)] += 1
    items = c.most_common(top) if top else sorted(c.items(),
                                                  key=lambda x: (-x[1], x[0]))
    return [k for k, _ in items], [n for _, n in items]


def per_day(rows):
    c = Counter()
    for r in rows:
        ts = r.get('_submission_time')
        if not ts:
            continue
        c[str(ts)[:10]] += 1
    days = sorted(c)
    return days, [c[d] for d in days]


GAD7 = ['gad7_nervous', 'gad7_uncontrol', 'gad7_excessive', 'gad7_relax',
        'gad7_restless', 'gad7_irritable', 'gad7_afraid']
GAD7_LBL = ['Nervioso', 'Sin control', 'Preocupación', 'Relajarse',
            'Inquieto', 'Irritable', 'Miedo']
PHQ2 = ['phq2_interest', 'phq2_depressed']
PHQ2_LBL = ['Poco interés', 'Decaído']


def mean_items(rows, fields):
    out = []
    for f in fields:
        vals = [int(r[f]) for r in rows if str(r.get(f, '')).isdigit()]
        out.append(round(sum(vals) / len(vals), 2) if vals else 0)
    return out


def gad7_score_dist(rows):
    buckets = {'Mínima (0-4)': 0, 'Leve (5-9)': 0,
               'Moderada (10-14)': 0, 'Severa (15-21)': 0}
    for r in rows:
        if all(str(r.get(f, '')).isdigit() for f in GAD7):
            s = sum(int(r[f]) for f in GAD7)
            if s <= 4:
                buckets['Mínima (0-4)'] += 1
            elif s <= 9:
                buckets['Leve (5-9)'] += 1
            elif s <= 14:
                buckets['Moderada (10-14)'] += 1
            else:
                buckets['Severa (15-21)'] += 1
    return list(buckets.keys()), list(buckets.values())


# --- HTML ------------------------------------------------------------------
def build_html(agg, total, updated):
    data_json = json.dumps(agg, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Encuesta población venezolana en Perú — Tablero en vivo</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{ --bg:#0f1419; --card:#1a2027; --ink:#e6edf3; --mut:#8b98a5; --ac:#4a86e8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:system-ui,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--ink); }}
  header {{ padding:22px 28px; border-bottom:1px solid #232b34; }}
  h1 {{ margin:0; font-size:20px; }}
  .sub {{ color:var(--mut); font-size:13px; margin-top:4px; }}
  .wrap {{ padding:24px 28px; max-width:1200px; margin:0 auto; }}
  .kpis {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:8px; }}
  .kpi {{ background:var(--card); border:1px solid #232b34; border-radius:12px; padding:18px 22px; min-width:160px; }}
  .kpi .n {{ font-size:34px; font-weight:700; }}
  .kpi .l {{ color:var(--mut); font-size:13px; margin-top:2px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:18px; margin-top:18px; }}
  .card {{ background:var(--card); border:1px solid #232b34; border-radius:12px; padding:16px 18px; }}
  .card h3 {{ margin:0 0 12px; font-size:14px; font-weight:600; color:var(--ink); }}
  canvas {{ max-height:300px; }}
  footer {{ color:var(--mut); font-size:12px; padding:18px 28px; border-top:1px solid #232b34; }}
</style></head>
<body>
<header>
  <h1>Encuesta para población venezolana en Perú</h1>
  <div class="sub">Tablero de seguimiento — datos agregados (sin información personal) · Actualizado: {updated}</div>
</header>
<div class="wrap">
  <div class="kpis">
    <div class="kpi"><div class="n">{total}</div><div class="l">Respuestas totales</div></div>
    <div class="kpi"><div class="n" id="kpi7">—</div><div class="l">Últimos 7 días</div></div>
    <div class="kpi"><div class="n" id="kpiday">—</div><div class="l">Día más activo</div></div>
  </div>
  <div class="grid">
    <div class="card"><h3>Respuestas por día</h3><canvas id="c_day"></canvas></div>
    <div class="card"><h3>Género</h3><canvas id="c_gender"></canvas></div>
    <div class="card"><h3>Departamento</h3><canvas id="c_depto"></canvas></div>
    <div class="card"><h3>Distrito (top 10)</h3><canvas id="c_distr"></canvas></div>
    <div class="card"><h3>Documento actual en Perú</h3><canvas id="c_doc"></canvas></div>
    <div class="card"><h3>Nivel educativo</h3><canvas id="c_edu"></canvas></div>
    <div class="card"><h3>Tipo de ingreso al Perú</h3><canvas id="c_entry"></canvas></div>
    <div class="card"><h3>¿Tiene cuenta de ahorros?</h3><canvas id="c_bank"></canvas></div>
    <div class="card"><h3>¿Está empleado/a?</h3><canvas id="c_emp"></canvas></div>
    <div class="card"><h3>GAD-7: promedio por ítem (0–3)</h3><canvas id="c_gad"></canvas></div>
    <div class="card"><h3>GAD-7: severidad (puntaje 0–21)</h3><canvas id="c_gadsev"></canvas></div>
    <div class="card"><h3>PHQ-2: promedio por ítem (0–3)</h3><canvas id="c_phq"></canvas></div>
  </div>
</div>
<footer>Fuente: KoboToolbox · Generado automáticamente · Solo agregados — ninguna respuesta individual es identificable.</footer>
<script>
const D = {data_json};
const AC = '#4a86e8', GRID = '#232b34', INK = '#8b98a5';
Chart.defaults.color = INK; Chart.defaults.borderColor = GRID;
const PAL = ['#4a86e8','#43d692','#ffad47','#f6919b','#a479e2','#16a766','#f2c960','#e06666','#76a5af','#c27ba0'];
function bar(id, labels, data, horizontal) {{
  if(!labels.length) return;
  new Chart(document.getElementById(id), {{ type:'bar',
    data:{{ labels, datasets:[{{ data, backgroundColor:AC }}] }},
    options:{{ indexAxis: horizontal?'y':'x', plugins:{{legend:{{display:false}}}},
      scales:{{ x:{{grid:{{color:GRID}}}}, y:{{grid:{{color:GRID}}}} }} }} }});
}}
function donut(id, labels, data) {{
  if(!labels.length) return;
  new Chart(document.getElementById(id), {{ type:'doughnut',
    data:{{ labels, datasets:[{{ data, backgroundColor:PAL }}] }},
    options:{{ plugins:{{legend:{{position:'bottom'}}}} }} }});
}}
function line(id, labels, data) {{
  new Chart(document.getElementById(id), {{ type:'line',
    data:{{ labels, datasets:[{{ data, borderColor:AC, backgroundColor:'rgba(74,134,232,.2)', fill:true, tension:.3 }}] }},
    options:{{ plugins:{{legend:{{display:false}}}}, scales:{{ x:{{grid:{{color:GRID}}}}, y:{{grid:{{color:GRID}}}} }} }} }});
}}
line('c_day', D.day[0], D.day[1]);
donut('c_gender', D.gender[0], D.gender[1]);
bar('c_depto', D.depto[0], D.depto[1], true);
bar('c_distr', D.distr[0], D.distr[1], true);
bar('c_doc', D.doc[0], D.doc[1], true);
bar('c_edu', D.edu[0], D.edu[1], true);
donut('c_entry', D.entry[0], D.entry[1]);
donut('c_bank', D.bank[0], D.bank[1]);
donut('c_emp', D.emp[0], D.emp[1]);
bar('c_gad', D.gad[0], D.gad[1], false);
bar('c_gadsev', D.gadsev[0], D.gadsev[1], false);
bar('c_phq', D.phq[0], D.phq[1], false);
// KPIs derivados
const days = D.day[0], dc = D.day[1];
if(days.length) {{
  const last7 = dc.slice(-7).reduce((a,b)=>a+b,0);
  document.getElementById('kpi7').textContent = last7;
  const mi = dc.indexOf(Math.max(...dc));
  document.getElementById('kpiday').textContent = days[mi];
}}
</script>
</body></html>"""


def main():
    token = os.environ.get('KOBO_TOKEN')
    if not token:
        sys.exit('ERROR: falta la variable de entorno KOBO_TOKEN.')
    subs = fetch_all(token)
    rows = to_leaf_rows(subs)
    total = len(rows)

    agg = {
        'day':    list(per_day(rows)),
        'gender': list(counts(rows, 'gender')),
        'depto':  list(counts(rows, 'departamento', top=12)),
        'distr':  list(counts(rows, 'distrito', top=10)),
        'doc':    list(counts(rows, 'main_current_doc_pe')),
        'edu':    list(counts(rows, 'edu_level')),
        'entry':  list(counts(rows, 'entry_regular')),
        'bank':   list(counts(rows, 'has_bank_account')),
        'emp':    list(counts(rows, 'employed')),
        'gad':    [GAD7_LBL, mean_items(rows, GAD7)],
        'gadsev': list(gad7_score_dist(rows)),
        'phq':    [PHQ2_LBL, mean_items(rows, PHQ2)],
    }
    updated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    (HERE / 'index.html').write_text(build_html(agg, total, updated),
                                     encoding='utf-8')
    print(f'OK — {total} respuestas → index.html ({updated})')


if __name__ == '__main__':
    main()
