# Web — the map

Next.js 16 (App Router) + TypeScript + MapLibre GL. Spanish first, English toggle.

```bash
scripts/dev.sh web        # http://localhost:3000  → redirects to /es
```

It reads `apps/api`, so run both (`scripts/dev.sh`) or the map will be empty.
`NEXT_PUBLIC_API_URL` overrides the default `http://localhost:8000`.

## i18n comes first, not last

`next-intl` was wired before the first component was written. Retrofitting means
touching every string in the app, and the plan is Spanish-first with an English toggle.

The locale is a URL segment — `/es`, `/en` — resolved in `src/proxy.ts` (Next 16's
rename of `middleware.ts`). Strings live in `messages/es.json` and `messages/en.json`;
nothing hardcodes a user-facing string in a component. Times are formatted in
`Europe/Madrid` regardless of where the browser is, because the reader is standing in
Madrid.

## MapLibre only

125-odd vehicles is well inside what MapLibre draws by itself, and a tool enters this
project when a requirement demands it. deck.gl is Stage 2 and gets added when the frame
rate says so, not before.

The basemap is [OpenFreeMap](https://openfreemap.org) Liberty — free, no account, no
key, no usage cliff. Same reasoning that chose MapLibre over Mapbox.

## Layers, in build order

| Source | Layer | From |
|---|---|---|
| `lines` | route geometry, coloured per line | `dimensions.cercanias_line_shapes` — the track, not the stopping pattern |
| `stations` | circles, plus labels from zoom 11 | `dimensions.cercanias_stations` |
| `vehicles` | circles coloured by line | Renfe's live feed, named from our schedule |

Clicking a train opens a card: line, train number, where it is, where it terminates, how
many stations it calls at.

## Polling and staleness

`/api/vehicles` is polled every 30 seconds, and not at all while the tab is hidden — a
background tab should not keep hitting a third party. The header shows the feed's own
observation time; past 90 seconds (two missed polls) it turns amber and says how old the
data is instead. If the API reports `upstream_ok: false`, it says Renfe is not responding
and keeps drawing the last known positions.

## What is deliberately not here

- No test runner. Adding one is a new dependency and needs an entry in
  `docs/learning/tool-choices.md` first. `scripts/check.sh` runs `npm test --if-present`,
  so it will pick one up the moment it exists.
- No visual design decisions treated as settled. `src/app/globals.css` is plain chrome so
  the network is what you look at; colours, copy and map style are Cole's call.
