# Pulso Madrid

*Madrid, en tiempo real y a través del tiempo.*

An urban-intelligence platform for Madrid: live mobility data (Cercanías) combined
with long-term neighborhood change (demographics, housing, commerce, development).

**Status:** early. Stage 1 — understand the data, model it, deploy a skeleton.

## Layout

| Path | What |
|---|---|
| `apps/web/` | Next.js + TypeScript frontend, MapLibre / deck.gl |
| `apps/api/` | FastAPI backend |
| `pipelines/` | Ingestion and transform jobs (Cloud Run Jobs) |
| `infrastructure/` | Terraform |
| `data-samples/` | Small committed samples used to reason about schemas |
| `scripts/` | Local dev helpers |

## Local development

```bash
scripts/setup.sh    # one-time: toolchain check + deps
scripts/dev.sh      # run web + api
scripts/check.sh    # lint, typecheck, test
```

## Data sources

Madrid Ayuntamiento open data, CRTM GTFS, Renfe Cercanías GTFS-RT, INE, Catastro,
OpenStreetMap (ODbL). Attribution and licensing notes live with each pipeline.
