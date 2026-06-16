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
GitHub Actions (cron cada 30 min)  →  regenera y commitea index.html
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
| `build_dashboard.py` | Generador (solo stdlib). Lee `KOBO_TOKEN` del entorno. |
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

## Notas

- `cron: '13,43 * * * *'` → cada 30 min. GitHub puede retrasar los cron unos minutos.
- Para añadir/quitar gráficos, edita la sección de agregaciones y el HTML en
  `build_dashboard.py`. Si cambian las preguntas del formulario, regenera `labels.json`
  con `extract_labels.py`.
