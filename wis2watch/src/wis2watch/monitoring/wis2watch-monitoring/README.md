# wis2watch-monitoring components

The Vue islands that the Django templates mount. Each island is one entry in
`vite.config.js`, mounted by `createWis2WatchApp` (`src/core.js`) into an
element the template renders, with props read off that element's `data-`
attributes.

| Entry                 | Mounts into           | Rendered by                                                       |
|-----------------------|-----------------------|-------------------------------------------------------------------|
| `ingest-monitor-map`  | `#ingest-monitor-map` | `monitoring/templates/wis2watchmonitoring/ingest_monitor_map.html` |
| `node-statistics`     | `#node-statistics`    | `core/templates/wis2watchcore/node_statistics.html`                 |

`monitoring/tests/test_bundle.py` holds this table to its word for everything
it can reach from Python: every entry has a bundle, no bundle is committed
that no entry names, and the map still dials the path the feed is served at.
The table itself is the part nothing checks, so it is the part to update by
hand.

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

### Run the unit tests

```sh
npm test
```

Vitest, over the plain modules: the composables and the small helpers beside
the components. Nothing here renders a component -- what broke this island in
#20 was arithmetic on the wrong key, not markup, so a component that holds no
logic of its own needs no test, and one that does is better off handing the
logic to a module beside it.

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
  `assets/shared.js`, which both entries import. A hashed name would leave the
  previous chunk behind on every rebuild, and an orphaned bundle still being
  served is the expensive kind of stale. Fingerprinting for cache-busting is
  Django's job instead, at `collectstatic`
  (`wis2watch.utils.staticfiles.ModuleAwareManifestStaticFilesStorage`).
