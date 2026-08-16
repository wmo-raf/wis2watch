# 1. Vue islands in Wagtail admin pages

Date: 2026-08-16

Status: Accepted

Resolves [#45](https://github.com/wmo-raf/wis2watch/issues/45), part of the
node statistics dashboard map, [#42](https://github.com/wmo-raf/wis2watch/issues/42).

## Context

The node statistics dashboard is a Vue island in an admin page, API-backed —
decided on the map. Before any of it can be drawn, three things have to be
settled: how the island mounts inside a Wagtail admin template, how a second
view of a node is reached, and how the bundle gets built and shipped.

One correction to the ticket's premise, found on the way in: the existing
monitoring map is **already** an admin page —
`monitoring/templates/wis2watchmonitoring/ingest_monitor_map.html` extends
`wagtailadmin/generic/base.html` and is registered through
`register_admin_urls`. The mounting pattern was not missing; it was
unremarked.

## Decision

### Mounting

Reuse what the map does: a template extending
`wagtailadmin/generic/base.html` renders an empty `<div id="...">` carrying
its props as `data-` attributes, and loads its entry in
`{% block extra_js %}` with `{% vue_bundle_url '<entry>' %}` after
`{{ block.super }}`. `createWis2WatchApp` (`src/core.js`) reads the dataset
through `convertDatasetToProps`, which coerces each value to the type the
component declares, so `data-node-id="12"` arrives as a number.

One difference from the map: the mount point goes in
`{% block main_content %}`, which keeps Wagtail's `nice-padding` wrapper and
the slim header. The map uses `{% block content %}` and then flattens the
padding with `.content { padding: 0 !important }`, because a map wants the
whole viewport. A dashboard sitting among admin furniture does not.

Anything reversible is reversed in Python and handed over as an attribute.
The bundle is built ahead of time; a path assembled inside it is a path
nobody can rename from the Django side.

### The tab

**A second URL rendering the same shell**, not a `w-tabs` panel in one
document. `node-detail/<id>/` and `node-detail/<id>/statistics/` both extend
`wis2watchcore/node_shell.html`, which renders the trail, a strip of tab
links, and the view's own body. The strip is Wagtail's `w-tabs` classes on
plain links, with the underline following `aria-selected` — CSS, no Stimulus
controller.

Why pages rather than panels:

- **The dashboard owns its query string.** Dashboard state syncs to the
  query string so any view of it is a shareable link. One document sharing a
  query string between a diagnostic snapshot and a moving dashboard would
  have made both harder to reason about.
- **The sync POST needs nothing.** `node_details` posts to itself; a POST
  lands back on the view it was made from. Wagtail's tabs write the open tab
  into the fragment, which a form POST is not obliged to carry — a class of
  bug that simply does not arise here.
- **Nobody pays for a view they did not open.** The statistics bundle and its
  API calls load on the statistics URL only.

The shell deliberately does *not* carry the node header grid. Filling it
costs a whole `node_detail()` run, and the statistics view reads rollups;
which node this is, the breadcrumb trail already says.

### Build and ship

Keep committing the built bundles, and add the statistics island as a second
Vite entry. Measured rather than assumed:

- `npm run build` on an unchanged tree reproduces the committed map bundle
  **byte for byte**, so adding an entry does not churn the other one.
- Rollup splits what the two entries share into one chunk both import
  (432 kB), leaving `node-statistics.js` at **90 kB** — the statistics page
  does not ship the map's copy of maplibre.
- Chunk names are **pinned rather than hashed** (`chunkFileNames:
  'assets/[name].js'`). For committed output a hash means every rebuild adds
  a file and orphans the last one.
- Each island now carries **only its own CSS** (`relativeCSSInjection`).
  The plugin's default puts every stylesheet in the build into every entry —
  invisible with one island, and with two it had the statistics page
  injecting the map's sidebar and maplibre styles into an admin page.

Because the chunk name is pinned, cache-busting has to come from Django, and
its stock `ManifestStaticFilesStorage` fingerprints references in CSS but not
in JavaScript. Production now uses
`wis2watch.utils.staticfiles.ModuleAwareManifestStaticFilesStorage`, which
turns on Django's ES-module import aggregation so the entry's
`import ... from "./assets/index.js"` points at the fingerprinted name.

That switch applies to every collected `.js`, not only the islands, and
aggregation fails `collectstatic` loudly on an import it cannot resolve —
which is the point, but it is a new way for a deploy to stop. Checked rather
than assumed: `collectstatic` over the whole real tree post-processes all 533
files and rewrites the entry's import to the hashed chunk.

Building in CI instead of committing stays open on the map. Nothing here
guards against a bundle committed stale; the byte-reproducible build makes
such a check cheap to add when CI arrives.

### The admin theme

PrimeVue was told `darkModeSelector: '.w-theme-dark'`, which is only the class
Wagtail sets for a reader who has *explicitly chosen* dark. Wagtail's default
is `w-theme-system`, where the OS decides — so on a dark admin the island
stayed light for everybody who never opened the setting, which is most
people. It is now told both, using PrimeVue's `[CSS]` escape hatch to place
the dark tokens inside a rule of our own:

```js
darkModeSelector: [
    '.w-theme-dark',
    '@media (prefers-color-scheme: dark) { .w-theme-system { [CSS] } }',
]
```

Verified in a browser against the built bundle: the dark tokens land under
both selectors, and an explicit light choice matches neither.

## Consequences

- A node has two views and a tab strip; adding a third is a URL, a
  `tab_content` block, and a line in `includes/node_tabs.html`.
- The statistics API and rendering tickets can assume the frame: the island
  mounts, is scoped to one node, and is handed its props.
- `src/` and `../static/vue/` must be committed together. A change to one
  without the other ships nothing, or ships something nobody can find.
