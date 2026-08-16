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
                "mqtt-monitor-map": resolve('./src/mqtt-monitor-map.js'),
                "node-statistics": resolve('./src/node-statistics.js'),
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
                chunkFileNames: 'assets/[name].js',
            },
        },
    },
})