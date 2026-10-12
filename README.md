# Tablero en vivo — Encuesta población venezolana en Perú

Dashboard **público y autoactualizable** de los datos agregados de la encuesta
(KoboToolbox), pensado para compartir avance y resultados con BID / actores externos
por un solo enlace.

**Cómo funciona**

```
build_dashboard.py  →  lee la API de Kobo (token = secret KOBO_TOKEN)
                       FILTRA la PII, calcula SOLO agregados
                       genera index.html
        │
GitHub Actions (cron cada 15 min)  →  regenera y commitea index.html
        │
tracker.py  →  lee la hoja de seguimiento (Drive, cuenta de servicio = secret
               GDRIVE_SA_KEY), cruza nombres contra Kobo EN MEMORIA y emite
               SOLO conteos (reconciliación enviados vs respuesta, sin PII)
        │
GitHub Pages  →  publica index.html  →  https://<usuario>.github.io/<repo>
```

> ⚠️ **Privacidad.** El generador excluye nombres, `ruc`, `recontact`, campos `*_other`,
> GPS y todos los metadatos de Kobo. El HTML contiene **únicamente conteos/promedios**,
> nunca respuestas individuales. El token **no** está en el repo: vive como *secret*
> de GitHub Actions.

## Archivos

| Archivo | Qué es |
|---|---|
| `build_dashboard.py` | Generador. Lee `KOBO_TOKEN` del entorno; arma las **pestañas** (Principal, Demografía, Pre/Post del corte, Calidad de datos, Enviados vs Respuesta). |
| `tracker.py` | Reconciliación **anonimizada** con la hoja de Drive (cuenta de servicio). Matching por nombre en memoria → solo conteos. Requiere `google-auth`, `requests`, `openpyxl`. |
| `labels.json` | Mapa código→etiqueta extraído del XLSForm (para mostrar nombres legibles). |
| `extract_labels.py` | Regenera `labels.json` desde el xlsx del instrumento (uso local; requiere openpyxl). |
| `index.html` | Salida publicada (se regenera sola). |
| `.github/workflows/update.yml` | Cron de GitHub Actions + publicación. |

## Probar localmente

```bash
KOBO_TOKEN=<tu_token> python3 build_dashboard.py
xdg-open index.html
```

## Puesta en marcha (una sola vez)

1. Crear el repo (público, para Pages gratis) y subir esta carpeta.
2. **Settings → Secrets and variables → Actions → New repository secret:**
   `KOBO_TOKEN = <token de Kobo>`.
3. **Settings → Pages → Source: Deploy from a branch → `main` / `(root)`**.
4. **Actions → "Actualizar dashboard" → Run workflow** (primera corrida manual).
5. El sitio queda en `https://<usuario>.github.io/<repo>`; se actualiza cada 30 min.

> Si rotas el token de Kobo, actualiza el secret `KOBO_TOKEN` (no hay que tocar el código).

## Pestaña "Enviados vs Respuesta" (reconciliación anonimizada)

Cruza la hoja de seguimiento de campo (Google Drive, con nombres/teléfonos) contra las
respuestas de Kobo y publica **solo conteos** (el matching por nombre ocurre en memoria;
ningún dato personal llega al HTML). Hay **dos formas** de alimentarla:

### A) Snapshot en el repo — la vía fácil (por defecto, sin setup de Google)

`tracker_agg.json` (solo conteos, se commitea) lo lee el dashboard. Se regenera a pedido:

1. Actualizar la hoja local `07_Data_Quality/inputs/seguimiento_google.xlsx` (bajarla del
   link de Drive — o pedirle a Claude que la baje).
2. Tener el último export de KOBO (`.xlsx`) en `live_dashboard/`.
3. `python3 make_tracker_snapshot.py` → escribe `tracker_agg.json` (imprime los conteos y
   verifica que no haya PII).
4. commit + push. La pestaña muestra "Reconciliación actualizada al <fecha>".

No necesita cuenta de servicio ni que Paulo comparta nada. Contra: se refresca cuando
corres el script, no cada 15 min (los tabs de Kobo sí van cada 15 min).

### B) En vivo con cuenta de servicio — opcional (auto cada 15 min)

Si quieres que la reconciliación también se actualice sola cada 15 min:

1. **Cuenta de servicio (Google Cloud):** proyecto + **Google Drive API** + service account
   + descargar la **llave JSON**.
2. **Secret** `GDRIVE_SA_KEY` en el repo (pegar el JSON).
3. **Paulo comparte** la hoja *"Marcha blanca_Promotor…"* con el email de la cuenta de
   servicio (rol Lector). `FILE_ID` fijo en `tracker.py` (o env `TRACKER_FILE_ID`).

Si `GDRIVE_SA_KEY` existe, se usa la vía en vivo; si no, cae al snapshot; si no hay
ninguno, la pestaña dice "no disponible". Umbrales/vocabulario de `Status` en `tracker.py`.

> El `Status` del tracker es manual y poco confiable ("Contesto" = contestó el WhatsApp,
> no que completó; solo "Hizo la encuesta" pretende completada). La verdad de completación
> es KOBO — por eso el numerador de la tasa de respuesta es la presencia real en Kobo.

## Notas

- `cron: '*/15 * * * *'` → cada 15 min. GitHub puede retrasar los cron unos minutos.
- Si falta `GDRIVE_SA_KEY` (o la hoja no está compartida con la cuenta de servicio), el
  dashboard igual se genera con todo lo de Kobo y la pestaña "Enviados vs Respuesta" muestra
  "no disponible" (degradación elegante).
- Para añadir/quitar gráficos, edita la sección de agregaciones y el HTML en
  `build_dashboard.py`. Si cambian las preguntas del formulario, regenera `labels.json`
  con `extract_labels.py`.
