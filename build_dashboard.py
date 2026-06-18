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


SEV_BANDS = ['Mínima (0-4)', 'Leve (5-9)', 'Moderada (10-14)', 'Severa (15-21)']


def gad7_band(score):
    if score <= 4:
        return SEV_BANDS[0]
    if score <= 9:
        return SEV_BANDS[1]
    if score <= 14:
        return SEV_BANDS[2]
    return SEV_BANDS[3]


def dist(rows, field, order=None):
    """Conteos en un orden fijo de códigos (escalas ordinales) o por frecuencia."""
    if order is None:
        return counts(rows, field)
    c = Counter()
    for r in rows:
        v = r.get(field)
        if v in (None, ''):
            continue
        c[str(v)] += 1
    labels = [label(field, code) for code in order]
    return labels, [c.get(str(code), 0) for code in order]


def crosstab(rows, field_a, field_b, order_a=None, order_b=None, normalize=True):
    """Tabla cruzada A×B para barras apiladas.

    Devuelve {'cats': [etiquetas de A], 'series': [{'name': etiqueta B,
    'data': [...]}, ...]}. Con normalize=True cada columna de A suma 100 (%)
    para comparar grupos de distinto tamaño. Ambos campos quedan en `rows`,
    así que nunca emite valores individuales: solo conteos agregados.
    """
    table = defaultdict(Counter)
    for r in rows:
        a, b = r.get(field_a), r.get(field_b)
        if a in (None, '') or b in (None, ''):
            continue
        table[str(a)][str(b)] += 1
    codes_a = order_a or sorted(table, key=lambda k: (-sum(table[k].values()), k))
    codes_a = [c for c in codes_a if c in table]
    b_seen = set()
    for col in table.values():
        b_seen |= set(col)
    codes_b = [c for c in (order_b or sorted(b_seen)) if c in b_seen]
    cats = [label(field_a, c) for c in codes_a]
    series = []
    for cb in codes_b:
        data = []
        for ca in codes_a:
            n = table[ca].get(cb, 0)
            tot = sum(table[ca].values())
            data.append(round(100 * n / tot, 1) if (normalize and tot) else n)
        series.append({'name': label(field_b, cb), 'data': data})
    return {'cats': cats, 'series': series}


def gad_sev_by_group(rows, group_field='main_current_doc_pe', order_a=None):
    """Distribución de severidad GAD-7 (banda) cruzada por tipo de documento.

    Estructura idéntica a crosstab() (apilada, normalizada a %)."""
    table = defaultdict(Counter)
    for r in rows:
        g = r.get(group_field)
        if g in (None, '') or not all(str(r.get(f, '')).isdigit() for f in GAD7):
            continue
        band = gad7_band(sum(int(r[f]) for f in GAD7))
        table[str(g)][band] += 1
    codes_a = order_a or sorted(table, key=lambda k: (-sum(table[k].values()), k))
    codes_a = [c for c in codes_a if c in table]
    cats = [label(group_field, c) for c in codes_a]
    series = []
    for band in SEV_BANDS:
        data = []
        for ca in codes_a:
            n = table[ca].get(band, 0)
            tot = sum(table[ca].values())
            data.append(round(100 * n / tot, 1) if tot else 0)
        series.append({'name': band, 'data': data})
    return {'cats': cats, 'series': series}


def pct_value(rows, field, code):
    """% de filas con `field` == code (entre las respondidas). Solo agregado."""
    code = str(code)
    answered = [r for r in rows if r.get(field) not in (None, '')]
    if not answered:
        return 0
    hit = sum(1 for r in answered if str(r.get(field)) == code)
    return round(100 * hit / len(answered))


def recontact_agg(subs):
    """Conteo Sí/No de disposición a ser recontactado, leído de las submissions
    crudas (el leaf `recontact` se mantiene fuera de `rows` por la regla de PII).
    Es un valor sí/no, no un dato de contacto: solo se emiten los dos conteos."""
    c = Counter()
    for s in subs:
        for k, v in s.items():
            if leaf(k) == 'recontact' and v not in (None, ''):
                c[str(v)] += 1
    return ['Sí', 'No'], [c.get('1', 0), c.get('2', 0)]


# --- HTML ------------------------------------------------------------------
def build_html(agg, total, updated, consent_pct, recontact_pct):
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
  .section {{ margin:30px 0 2px; padding-bottom:8px; border-bottom:1px solid #232b34; }}
  .section h2 {{ margin:0; font-size:16px; font-weight:700; color:var(--ink); }}
  .section .d {{ color:var(--mut); font-size:12px; margin-top:3px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:18px; margin-top:14px; }}
  .card {{ background:var(--card); border:1px solid #232b34; border-radius:12px; padding:16px 18px; }}
  .card.wide {{ grid-column:1 / -1; }}
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
    <div class="kpi"><div class="n" id="kpiavg">—</div><div class="l">Promedio diario (7d)</div></div>
    <div class="kpi"><div class="n" id="kpiday">—</div><div class="l">Día más activo</div></div>
    <div class="kpi"><div class="n">{consent_pct}%</div><div class="l">Tasa de consentimiento</div></div>
    <div class="kpi"><div class="n">{recontact_pct}%</div><div class="l">Acepta recontacto</div></div>
  </div>

  <div class="section"><h2>Seguimiento de campo</h2>
    <div class="d">Ritmo de respuestas y disposición de los participantes.</div></div>
  <div class="grid">
    <div class="card wide"><h3>Respuestas por día y acumulado</h3><canvas id="c_day"></canvas></div>
    <div class="card"><h3>Género</h3><canvas id="c_gender"></canvas></div>
    <div class="card"><h3>Departamento</h3><canvas id="c_depto"></canvas></div>
    <div class="card"><h3>Distrito (top 10)</h3><canvas id="c_distr"></canvas></div>
    <div class="card"><h3>Consentimiento informado</h3><canvas id="c_consent"></canvas></div>
    <div class="card"><h3>¿Acepta ser recontactado/a?</h3><canvas id="c_recon"></canvas></div>
  </div>

  <div class="section"><h2>Regularización y documentos · núcleo RDD</h2>
    <div class="d">Resultados clave según el documento migratorio vigente (% por columna).</div></div>
  <div class="grid">
    <div class="card"><h3>Documento actual en Perú</h3><canvas id="c_doc"></canvas></div>
    <div class="card"><h3>Tipo de ingreso al Perú</h3><canvas id="c_entry"></canvas></div>
    <div class="card wide"><h3>Empleo según documento (% por documento)</h3><canvas id="c_docemp"></canvas></div>
    <div class="card wide"><h3>Cuenta bancaria según documento (% por documento)</h3><canvas id="c_docbank"></canvas></div>
    <div class="card wide"><h3>Nivel educativo según documento (% por documento)</h3><canvas id="c_docedu"></canvas></div>
    <div class="card"><h3>Nivel educativo</h3><canvas id="c_edu"></canvas></div>
    <div class="card"><h3>¿Sabía del cierre del PTP?</h3><canvas id="c_aware"></canvas></div>
    <div class="card"><h3>¿Recuerda la fecha de corte?</h3><canvas id="c_cutoff"></canvas></div>
    <div class="card"><h3>¿Solicitó protección?</h3><canvas id="c_applied"></canvas></div>
    <div class="card"><h3>Resultado de la solicitud de protección</h3><canvas id="c_protout"></canvas></div>
  </div>

  <div class="section"><h2>Bienestar · salud mental</h2>
    <div class="d">Ansiedad (GAD-7) y depresión (PHQ-2). Promedios e índice de severidad.</div></div>
  <div class="grid">
    <div class="card"><h3>GAD-7: promedio por ítem (0–3)</h3><canvas id="c_gad"></canvas></div>
    <div class="card"><h3>GAD-7: severidad (puntaje 0–21)</h3><canvas id="c_gadsev"></canvas></div>
    <div class="card"><h3>PHQ-2: promedio por ítem (0–3)</h3><canvas id="c_phq"></canvas></div>
    <div class="card wide"><h3>Severidad GAD-7 según documento (% por documento)</h3><canvas id="c_gaddoc"></canvas></div>
  </div>

  <div class="section"><h2>Economía y sociedad</h2>
    <div class="d">Suficiencia de ingresos, vulnerabilidad, discriminación e intención de permanencia.</div></div>
  <div class="grid">
    <div class="card"><h3>¿El ingreso del hogar alcanza?</h3><canvas id="c_income"></canvas></div>
    <div class="card"><h3>Inseguridad alimentaria</h3><canvas id="c_food"></canvas></div>
    <div class="card"><h3>Fragilidad financiera</h3><canvas id="c_frag"></canvas></div>
    <div class="card"><h3>Discriminación por nacionalidad</h3><canvas id="c_discrim"></canvas></div>
    <div class="card"><h3>Intención de quedarse en Perú</h3><canvas id="c_stay"></canvas></div>
    <div class="card"><h3>¿Está empleado/a?</h3><canvas id="c_emp"></canvas></div>
    <div class="card"><h3>¿Tiene cuenta de ahorros?</h3><canvas id="c_bank"></canvas></div>
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
function dayChart(id, labels, daily, cum) {{
  if(!labels.length) return;
  const roll = daily.map((_,i)=>{{ const s=daily.slice(Math.max(0,i-6),i+1);
    return Math.round(s.reduce((a,b)=>a+b,0)/s.length*10)/10; }});
  new Chart(document.getElementById(id), {{ type:'bar',
    data:{{ labels, datasets:[
      {{ label:'Por día', data:daily, backgroundColor:AC, order:3, yAxisID:'y' }},
      {{ label:'Media móvil 7d', data:roll, type:'line', borderColor:'#ffad47', backgroundColor:'transparent', tension:.3, order:2, yAxisID:'y' }},
      {{ label:'Acumulado', data:cum, type:'line', borderColor:'#43d692', backgroundColor:'rgba(67,214,146,.12)', fill:true, tension:.3, order:1, yAxisID:'y1' }} ]}},
    options:{{ plugins:{{legend:{{position:'bottom'}}}},
      scales:{{ x:{{grid:{{color:GRID}}}},
        y:{{position:'left', grid:{{color:GRID}}, title:{{display:true, text:'Por día'}}}},
        y1:{{position:'right', grid:{{drawOnChartArea:false}}, title:{{display:true, text:'Acumulado'}}}} }} }} }});
}}
function stacked(id, ct, asPct) {{
  if(!ct || !ct.cats.length) return;
  new Chart(document.getElementById(id), {{ type:'bar',
    data:{{ labels:ct.cats, datasets:ct.series.map((s,i)=>(
      {{ label:s.name, data:s.data, backgroundColor:PAL[i%PAL.length] }})) }},
    options:{{ indexAxis:'y', plugins:{{ legend:{{position:'bottom'}},
        tooltip:{{ callbacks:{{ label:c=>` ${{c.dataset.label}}: ${{c.parsed.x}}${{asPct?'%':''}}` }} }} }},
      scales:{{ x:{{ stacked:true, grid:{{color:GRID}}, max:asPct?100:undefined,
          ticks:{{ callback:v=>asPct?v+'%':v }} }},
        y:{{ stacked:true, grid:{{color:GRID}} }} }} }} }});
}}
dayChart('c_day', D.day[0], D.day[1], D.cum);
donut('c_gender', D.gender[0], D.gender[1]);
bar('c_depto', D.depto[0], D.depto[1], true);
bar('c_distr', D.distr[0], D.distr[1], true);
donut('c_consent', D.consent[0], D.consent[1]);
donut('c_recon', D.recontact[0], D.recontact[1]);
bar('c_doc', D.doc[0], D.doc[1], true);
donut('c_entry', D.entry[0], D.entry[1]);
stacked('c_docemp', D.doc_x_emp, true);
stacked('c_docbank', D.doc_x_bank, true);
stacked('c_docedu', D.doc_x_edu, true);
bar('c_edu', D.edu[0], D.edu[1], true);
bar('c_aware', D.aware[0], D.aware[1], true);
bar('c_cutoff', D.cutoff[0], D.cutoff[1], true);
donut('c_applied', D.applied[0], D.applied[1]);
donut('c_protout', D.protout[0], D.protout[1]);
bar('c_gad', D.gad[0], D.gad[1], false);
bar('c_gadsev', D.gadsev[0], D.gadsev[1], false);
bar('c_phq', D.phq[0], D.phq[1], false);
stacked('c_gaddoc', D.gad_x_doc, true);
bar('c_income', D.income[0], D.income[1], true);
donut('c_food', D.food[0], D.food[1]);
donut('c_frag', D.frag[0], D.frag[1]);
bar('c_discrim', D.discrim[0], D.discrim[1], true);
bar('c_stay', D.stay[0], D.stay[1], true);
donut('c_emp', D.emp[0], D.emp[1]);
donut('c_bank', D.bank[0], D.bank[1]);
// KPIs derivados
const days = D.day[0], dc = D.day[1];
if(days.length) {{
  const last7 = dc.slice(-7).reduce((a,b)=>a+b,0);
  document.getElementById('kpi7').textContent = last7;
  document.getElementById('kpiavg').textContent = (last7/Math.min(7,dc.length)).toFixed(1);
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

    # Órdenes fijos para escalas ordinales / códigos categóricos.
    DOC_ORDER = ['3', '2', '1', '5', '4', '6', '0']   # PTP→CPP→CE→refugio→DNI→vencido→ninguno
    YESNO = ['1', '2']
    EDU_ORDER = [str(i) for i in range(8)]            # 0..7
    INCOME_ORDER = ['1', '2', '3', '4']
    AWARE_ORDER = ['1', '2', '3', '88']
    CUTOFF_ORDER = ['1', '2', '3']
    STAY_ORDER = ['1', '2', '3', '4', '5']
    APPLIED_ORDER = ['1', '2', '3', '4']
    PROTOUT_ORDER = ['1', '2', '3', '4']

    days, dcounts = per_day(rows)
    cum, run = [], 0
    for n in dcounts:
        run += n
        cum.append(run)

    agg = {
        'day':       [days, dcounts],
        'cum':       cum,
        'gender':    list(counts(rows, 'gender')),
        'depto':     list(counts(rows, 'departamento', top=12)),
        'distr':     list(counts(rows, 'distrito', top=10)),
        'consent':   list(dist(rows, 'consent', ['1', '0'])),
        'recontact': list(recontact_agg(subs)),
        'doc':       list(dist(rows, 'main_current_doc_pe', DOC_ORDER)),
        'entry':     list(counts(rows, 'entry_regular')),
        'doc_x_emp':  crosstab(rows, 'main_current_doc_pe', 'employed', DOC_ORDER, YESNO),
        'doc_x_bank': crosstab(rows, 'main_current_doc_pe', 'has_bank_account', DOC_ORDER, YESNO),
        'doc_x_edu':  crosstab(rows, 'main_current_doc_pe', 'edu_level', DOC_ORDER, EDU_ORDER),
        'edu':       list(dist(rows, 'edu_level', EDU_ORDER)),
        'aware':     list(dist(rows, 'aware_ptp_closing', AWARE_ORDER)),
        'cutoff':    list(dist(rows, 'knows_cutoff', CUTOFF_ORDER)),
        'applied':   list(dist(rows, 'applied_protection', APPLIED_ORDER)),
        'protout':   list(dist(rows, 'protection_outcome', PROTOUT_ORDER)),
        'gad':       [GAD7_LBL, mean_items(rows, GAD7)],
        'gadsev':    list(gad7_score_dist(rows)),
        'phq':       [PHQ2_LBL, mean_items(rows, PHQ2)],
        'gad_x_doc': gad_sev_by_group(rows, 'main_current_doc_pe', DOC_ORDER),
        'income':    list(dist(rows, 'income_sufficiency', INCOME_ORDER)),
        'food':      list(counts(rows, 'food_insecurity')),
        'frag':      list(counts(rows, 'financial_fragility')),
        'discrim':   list(counts(rows, 'discrim_nationality')),
        'stay':      list(dist(rows, 'intention_stay', STAY_ORDER)),
        'emp':       list(counts(rows, 'employed')),
        'bank':      list(counts(rows, 'has_bank_account')),
    }
    consent_pct = pct_value(rows, 'consent', '1')
    recon_lbls, recon_vals = agg['recontact']
    rtot = sum(recon_vals)
    recontact_pct = round(100 * recon_vals[0] / rtot) if rtot else 0

    updated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    (HERE / 'index.html').write_text(
        build_html(agg, total, updated, consent_pct, recontact_pct),
        encoding='utf-8')
    print(f'OK — {total} respuestas → index.html ({updated})')


if __name__ == '__main__':
    main()
