# wis2watch-monitoring components

The Vue islands that the Django templates mount. Each island is one entry in
`vite.config.js`, mounted by `createWis2WatchApp` (`src/core.js`) into an
element the template renders, with props read off that element's `data-`
attributes.

| Entry              | Mounts into        | Rendered by                                              |
|--------------------|--------------------|----------------------------------------------------------|
| `mqtt-monitor-map` | `#mqtt-monitor-map`| `monitoring/templates/wis2watchmonitoring/ingest_monitor_map.html` |
| `node-statistics`  | `#node-statistics` | `core/templates/wis2watchcore/node_statistics.html`       |

## Project Setup

```sh
npm install
```

### Compile and Hot-Reload for Development

```sh
npm run dev
```

`VUE_FRONTEND_USE_DEV_SERVER` defaults to `DEBUG`, so with `DEBUG = True` the
templates ask the dev server for the entry rather than the built file, and
nothing needs rebuilding while you work.

### Compile and Minify for Production

```sh
npm run build
```

**The built output is committed**, to `../static/vue/`. Nothing rebuilds it on
the way to production, so a change in `src/` that is not followed by a build
and a commit of what the build produced is a change that never ships.

Two things make that arrangement survivable, and both are worth keeping:

- **The build is reproducible.** `npm run build` on an unchanged tree
  reproduces the committed bundles byte for byte, so a rebuild shows up in
  `git status` only where something actually changed.
- **Chunk names are pinned, not hashed** (`chunkFileNames`). What the entries
  share -- Vue, PrimeVue, the mount helper -- rollup splits into
  `assets/index.js`, which both entries import. A hashed name would leave the
  previous chunk behind on every rebuild, and an orphaned bundle still being
  served is the expensive kind of stale. Fingerprinting for cache-busting is
  Django's job instead, at `collectstatic`
  (`wis2watch.utils.staticfiles.ModuleAwareManifestStaticFilesStorage`).
