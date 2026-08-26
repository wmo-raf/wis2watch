import {fileURLToPath, URL} from 'node:url'
import {resolve} from 'path'

import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import cssInjectedByJsPlugin from "vite-plugin-css-injected-by-js";

// https://vite.dev/config/
export default defineConfig({
    base: '/static/vue/',
    plugins: [
        vue(),
        vueDevTools(),
        // Each island carries its own CSS and only its own. The plugin's
        // default is to put all of the build's CSS into every entry, which
        // with one island was the same thing and with two would have the
        // statistics page injecting the map's sidebar and maplibre styles
        // into a Wagtail admin page.
        cssInjectedByJsPlugin({
            relativeCSSInjection: true,
        }),
    ],
    resolve: {
        alias: {
            '@': fileURLToPath(new URL('./src', import.meta.url))
        },
    },
    build: {
        rollupOptions: {
            input: {
                "ingest-monitor-map": resolve('./src/ingest-monitor-map.js'),
                "node-statistics": resolve('./src/node-statistics.js'),
                "all-nodes": resolve('./src/all-nodes.js'),
            },
            output: {
                dir: '../static/vue/',
                entryFileNames: '[name].js',
                // What two islands share, rollup splits out for itself, and
                // the entry imports it: the statistics island does not carry
                // the map's copy of maplibre, and the browser fetches Vue
                // once for both. The name is pinned rather than hashed
                // because these files are committed: a hash in the name
                // means every rebuild adds a file and orphans the last one,
                // and orphaned bundles are how a page ends up served by code
                // nobody can find in the tree.
                //
                // Pinned to a literal rather than `[name]` for the same
                // reason. Rollup names a shared chunk after whichever module
                // inside it it likes, so the chunk was `index.js` until #76
                // shared one small file between the islands and it silently
                // became `theme.js` -- a rename is a new committed file and
                // an orphaned old one, which is the hazard above arriving by
                // a door the hash was not guarding.
                //
                // With three entries there is more than one shared chunk, and
                // Rollup settles the collision by numbering: `shared.js`,
                // `shared2.js`, `shared3.js`. That is fine and is not the
                // hazard above -- every one of them is emitted and committed
                // on the same build, and the entries' imports are rewritten in
                // the same pass, so no file is ever left behind. What the
                // numbers are *not* is stable: which chunk gets which suffix
                // follows emission order, so two of them can swap contents on
                // a build that changes nothing else. Read a diff of these as
                // "the split moved", never as "this chunk changed".
                //
                // The split is what makes three entries cheaper than two were:
                // `shared.js` is Vue and the island bootstrap that all three
                // want, `shared2.js` is what the map and the statistics tab
                // both pull in, and `shared3.js` is the sparkline and its
                // colour roles, which the statistics tab shares with the
                // homepage table. The homepage island fetches ~368 kB of the
                // 1.38 MB both entries used to carry between them.
                chunkFileNames: 'assets/shared.js',
            },
        },
    },
})