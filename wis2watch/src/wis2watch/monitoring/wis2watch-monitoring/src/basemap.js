/**
 * The basemap every map in this project stands on: one style URL per
 * lighting, the initial view, the controls, and a promise that resolves when
 * the style is loaded. One map uses it today, and the statistics map panel is
 * the second caller this shape was measured against.
 *
 * This is deliberately *not* a base map component. #50 extracted a shared map
 * from the ingest monitor's map and found that what survives extraction is about thirty
 * lines -- the style, the view, the controls, and the promise every
 * layer-adding caller needs and every caller gets wrong the first time. What
 * does not survive is everything a map does after that: markers, popups,
 * legends, selection. So this hands back a MapLibre map and gets out of the
 * way.
 *
 * The one thing it will not get out of the way of is the theme flip, because
 * a caller that forgets `transformStyle` loses its own layers with no error
 * (see below). That is not a rule a comment can enforce, so the flip lives
 * here and nowhere else.
 *
 * **One rule for whoever adds the first symbol layer: name one font, never a
 * stack.** `text-font: ['Noto Sans Regular']` is served; `['Noto Sans
 * Regular', 'Arial Unicode MS Regular']` 404s, because a stack is fetched as
 * one path built from the joined names and no font by that joined name
 * exists. The failure is silent and total -- a symbol layer whose font cannot
 * be fetched stops the whole source from tiling, so sibling circle layers
 * render nothing while `addLayer` returns cleanly and `source.loaded()`
 * reports `true`. #50 hit this and worked around it by naming a single font;
 * its diagnosis of *why* was wrong, and the research on #67 found the rule is
 * architectural rather than local -- OpenFreeMap, ICGC and VersaTiles all 404
 * stacks and serve single fonts. `Noto Sans Regular` and `Noto Sans Bold` are
 * the two this glyph endpoint serves.
 */
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import {isDarkTheme, watchTheme} from '@/theme.js'

/**
 * OpenFreeMap, one style per lighting. Keyless, CORS-open, no key material,
 * so the deployment story is the same as having no basemap at all. Settled by
 * the research on #67 after #66 made a dark counterpart mandatory: the tab's
 * two standings survive on Dark's `rgb(12,12,12)` ground at 10.5:1
 * (transmitting) and 7.1:1 (silent).
 *
 * Both styles carry their own attribution in their TileJSON and MapLibre's
 * default `AttributionControl` renders it, on its default terms. Nothing here
 * writes an attribution string and nothing configures the control: a
 * hand-written string goes stale the moment the style's sources change, and
 * the licences these tiles arrive under are not ours to fold away.
 *
 * **Two risks worth knowing before you rely on this.** OpenFreeMap is one
 * maintainer on two servers, donation-funded, with no SLA and a terms page
 * that says it may be discontinued at any time without notice; if that
 * happens, self-hosting is ~300 GB and ~EUR 5/mo and these two constants are
 * the whole change. And light and dark are *not* a matched pair -- Positron
 * has been cleaned, while OpenFreeMap's own repo calls Dark an unmodified
 * fork of an abandoned upstream, with more low-zoom symbol layers un-gated.
 * If Dark turns noisy, the place to quiet it is `transformStyle` below, which
 * already has both styles in its hands.
 *
 * The style these replaced, for a one-line revert if this goes wrong:
 * `https://geoserveis.icgc.cat/contextmaps/icgc_mapa_base_gris_simplificat.json`
 */
export const BASEMAP_STYLES = {
    light: 'https://tiles.openfreemap.org/styles/positron',
    dark: 'https://tiles.openfreemap.org/styles/dark',
}

/** The style URL for the lighting the page is in right now. */
export function basemapStyleUrl() {
    return isDarkTheme() ? BASEMAP_STYLES.dark : BASEMAP_STYLES.light
}

/**
 * The ids a style document brought with it, so that what a caller added on
 * top can be told apart from the basemap by subtraction.
 */
function idsIn(style) {
    return {
        sources: new Set(Object.keys(style.sources ?? {})),
        layers: new Set((style.layers ?? []).map((layer) => layer.id)),
    }
}

/**
 * A MapLibre map on the current theme's basemap, which restyles itself when
 * the theme moves.
 *
 * @param {HTMLElement} container
 * @param {{center: [number, number], zoom: number}} view - the initial view.
 * @returns {{map: import('maplibre-gl').Map, ready: Promise<import('maplibre-gl').Map>, destroy: () => void}}
 *     `ready` resolves once the style is loaded and it is safe to add sources
 *     and layers. `destroy` stops watching the theme and removes the map;
 *     callers must call it on unmount.
 */
export function createBaseMap(container, {center, zoom}) {
    const map = new maplibregl.Map({
        container,
        style: basemapStyleUrl(),
        center,
        zoom,
    })

    //: Bottom-right, which is where the shipped map has always had them and
    //: where neither map's own furniture sits.
    map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right')
    map.addControl(new maplibregl.FullscreenControl(), 'bottom-right')

    /**
     * What the *basemap* owns, as of the style currently loaded. Captured
     * before `ready` resolves, which is before any caller has had the chance
     * to add anything, so at this moment every id on the map is the
     * basemap's.
     */
    let basemapOwns = null

    const ready = new Promise((resolve) => {
        map.once('load', () => {
            basemapOwns = idsIn(map.getStyle())
            resolve(map)
        })
    })

    /**
     * The style URL the map is on. Moved by `transformStyle` rather than
     * here, because that callback runs only once the incoming style has
     * actually been fetched: a style that 404s leaves this naming the style
     * still on screen, and the next flip tries again instead of deciding it
     * has nothing to do.
     */
    let current = basemapStyleUrl()

    /**
     * **`setStyle` deletes our layers unless we stop it.** MapLibre diffs the
     * map's serialised style against the incoming one; the incoming style
     * knows nothing about a source we added, so the diff emits `removeSource`
     * and `removeLayer` for every one of ours and the map carries them out.
     * `transformStyle` is the documented way to carry state across a style
     * change, and it is mandatory here rather than decorative.
     *
     * Ours are appended after the incoming style's own layers, so the
     * basemap stays *under* them by construction rather than by luck.
     */
    function applyTheme() {
        const wanted = basemapStyleUrl()

        if (wanted === current) {
            return
        }

        map.setStyle(wanted, {
            transformStyle: (previous, next) => {
                //: Reached only once the incoming style has been fetched.
                current = wanted

                if (!previous || !basemapOwns) {
                    return next
                }

                const ours = {
                    sources: Object.fromEntries(
                        Object.entries(previous.sources ?? {})
                            .filter(([id]) => !basemapOwns.sources.has(id))
                    ),
                    layers: (previous.layers ?? [])
                        .filter((layer) => !basemapOwns.layers.has(layer.id)),
                }

                //: From here on, the incoming style is the basemap.
                basemapOwns = idsIn(next)

                return {
                    ...next,
                    sources: {...next.sources, ...ours.sources},
                    layers: [...next.layers, ...ours.layers],
                }
            },
        })
    }

    const stopWatchingTheme = watchTheme(applyTheme)

    return {
        map,
        ready,
        destroy() {
            stopWatchingTheme()
            map.remove()
        },
    }
}
