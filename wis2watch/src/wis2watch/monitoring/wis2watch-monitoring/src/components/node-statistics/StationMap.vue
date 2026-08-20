<template>
  <div ref="panel" class="station-map">
    <template v-if="collection.features.length">
      <div class="station-map__frame">
        <div ref="canvas" class="station-map__canvas"/>

        <!-- Top-left, which is the one corner MapLibre's own furniture does
             not use: the helper puts navigation and fullscreen bottom-right,
             and attribution is bottom-left on its own terms. -->
        <div class="station-map__legend">
          <p class="station-map__legend-title">
            Standing &mdash; flat {{ staleAfterHours }}h
          </p>
          <ul class="station-map__keys">
            <li class="station-map__key">
              <span class="station-map__swatch station-map__swatch--live"/>
              Transmitting
            </li>
            <li class="station-map__key">
              <span class="station-map__swatch station-map__swatch--silent"/>
              Silent
            </li>
          </ul>
          <p class="station-map__legend-note">
            The window control does not move this map.
          </p>
        </div>
      </div>

      <Message
          v-if="failure"
          severity="warn"
          :closable="false"
          class="station-map__failure"
      >
        The map reported a problem and may be drawing less than it says:
        {{ failure }}
      </Message>

      <!-- The residue, with its own silent count. A station with no
           coordinates inside an outage region is silent for the same reason
           as the ones drawn red and cannot be seen to be. -->
      <p v-if="missing.total" class="station-map__unplotted">
        {{ formatCount(missing.total) }} of
        {{ formatCount(stations.length) }} stations carry no coordinates and
        are not on this map
        <template v-if="missing.silent">
          &mdash; <strong>{{ formatCount(missing.silent) }} of them silent</strong>,
          which is a failure this map cannot show you.
        </template>
        <template v-else>
          &mdash; none of them silent.
        </template>
      </p>
    </template>

    <p v-else class="station-map__none">
      <template v-if="stations.length">
        None of this centre's {{ formatCount(stations.length) }} stations
        carries coordinates, so there is nothing to put on a map
        <template v-if="missing.silent">
          &mdash; including {{ formatCount(missing.silent) }} that have gone
          silent.
        </template>
      </template>
      <template v-else>
        This centre declares no stations, so there is nothing to map.
      </template>
    </p>
  </div>
</template>

<script setup>
/**
 * The stations on the ground: silent against transmitting, and nothing else.
 *
 * Spatial correlation is the only thing this panel can say that the table
 * beside it cannot -- a contiguous regional outage reads as a block of red at
 * a glance, and a scatter of individual deaths reads as a scatter. Everything
 * else about a station is better read as a row.
 *
 * **Two colours, not four.** Four survives at a thousand points only because
 * the working colour is red and the other three are all "not red".
 * `undeclared` and `never_transmitted` never formed a spatial pattern at any
 * density tried: "transmitting but nothing declares it" is a registry fact
 * with no geography. So the surface carries the same `SILENT` split the
 * figures and the rows do -- from the shared constant, not re-derived here --
 * and the full standing stays in the popup and the table.
 *
 * **The window control does not move this map, and the legend says so.**
 * "Reported in window" is degenerate at both ends of the range: at 24 hours
 * it *is* the silent standing by construction and draws the identical
 * picture, and at 90 days a block dead for weeks draws almost entirely green,
 * because a station that died three weeks ago did report inside the last 90
 * days. The standing on a row is the now-anchored one -- the same flat
 * threshold the figures block is counted over -- so this is the one panel a
 * reader can move a control past without it changing. The source is only
 * re-set when what is drawn actually differs, so that is a property of the
 * map rather than a hope about re-renders.
 *
 * **A GeoJSON source rather than the DOM markers `MQTTMap.vue` uses**, and
 * this is why the two maps are not one component. What they genuinely share
 * is the basemap helper and nothing else. A DOM marker is a fixed pixel size
 * at every zoom and paints in DOM order, so a thousand of them at country
 * zoom is an opaque carpet in which silent stations land underneath working
 * ones at random. A source fixes both at once: the radius interpolates with
 * the zoom, and **silent is a second layer drawn on top of transmitting**, so
 * a failure is never hidden under a station that is fine.
 *
 * No clustering and no heat surface, deliberately. A cluster mutes the
 * finding unless its circle carries a rate, and it costs the drilldown
 * gesture. A heat surface reads station *density* as failure density and
 * turns the capital into a red blob for having stations in it.
 *
 * **Two silent traps are designed around here, neither of which produces a
 * console error.** A symbol layer whose `text-font` the glyph endpoint does
 * not serve stops the whole source from tiling -- the circle layers beside it
 * render nothing while `addLayer` returns cleanly and `source.loaded()` is
 * `true` -- and comma-separated font stacks are what 404s (#67). This panel
 * names no font at all, because it draws no text on the canvas: what a
 * station is called is in the popup, which is DOM. And an `interpolate`
 * inside a `coalesce` throws into a promise nothing is listening to, leaving
 * the layers already added rendering and the missing one merely invisible --
 * so every source and layer here is added inside a `try`, and the map's own
 * `error` events are put on the page rather than left in the console.
 */
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import maplibregl from 'maplibre-gl'
import Message from 'primevue/message'

import {createBaseMap} from '@/basemap.js'
import {formatCount, formatInstant, formatQuiet} from './charts/plot.js'
import {boundsOf, stationFeatures, unplottable} from './geography.js'
import {STANDING_LABEL} from './standings.js'
import {useRoles} from './charts/useRoles.js'

const props = defineProps({
  /** The rows, exactly as the table has them. There is no map endpoint. */
  stations: {
    type: Array,
    required: true
  },
  /**
   * The station the reader picked, as a string because it arrives from the
   * address bar, or empty for none.
   */
  selected: {
    type: String,
    default: ''
  },
  /** How many hours of quiet is too many, echoed by the server. */
  staleAfterHours: {
    type: Number,
    default: 24
  },
})

const emit = defineEmits(['choose'])

//: One source, three layers, in the order they are drawn. Transmitting
//: underneath, silent over it -- a station that has stopped is never hidden
//: under one that is fine, at any zoom -- and the picked ring over both.
const SOURCE = 'stations'
const LIVE_LAYER = 'stations-transmitting'
const SILENT_LAYER = 'stations-silent'
const PICKED_LAYER = 'stations-picked'

//: How big a station is drawn, by zoom. This is the whole answer to "a
//: thousand points at country zoom": a fixed size is a carpet at z4 and a
//: scatter of specks at z10, and a DOM marker can be nothing else.
const RADIUS = ['interpolate', ['linear'], ['zoom'], 2, 2.2, 5, 3.6, 8, 5.5, 12, 8]

//: The ring on the picked station, a step clear of the dot underneath it.
const PICKED_RADIUS = ['interpolate', ['linear'], ['zoom'], 2, 5.5, 5, 7.5, 8, 10, 12, 13]

//: The halo separating one dot from the next where they crowd. It is the
//: page's ground rather than a colour of its own, so the tab spends no third
//: colour on a mark that means nothing.
const HALO = ['interpolate', ['linear'], ['zoom'], 2, 0.4, 8, 1.2]

//: The colours MapLibre needs as strings. Every other surface on the tab
//: writes `var(--stat-live)` and follows a theme flip with no JavaScript;
//: paint properties cannot, so they are resolved through the one composable
//: that knows how to read a role back and when to read it again.
const ROLES = ['live', 'silent', 'on-live', 'focus']

const panel = ref(null)
const canvas = ref(null)
const failure = ref('')

const roles = useRoles(panel, ROLES)

//: Only what is drawn: an id and a boolean. Everything the popup says is
//: looked up on the row when it is clicked, so a window change -- which
//: re-reads every row and re-derives every `hours_quiet` against the clock --
//: cannot make this collection different while the picture is the same.
const collection = computed(() => stationFeatures(props.stations))

const missing = computed(() => unplottable(props.stations))

//: The rows by id, for a popup that is opened long after the source was set.
const byId = computed(
    () => new Map(props.stations.map((station) => [station.station_id, station]))
)

//: The picked station as MapLibre will match it. The address bar carries a
//: string and the feature carries the number the server sent, so the
//: conversion happens once, here.
const pickedId = computed(() => {
  const id = Number(props.selected)

  return props.selected && Number.isFinite(id) ? id : null
})

let teardownMap = null
let map = null
let popup = null
//: Which station the popup on screen is about, so a pick made elsewhere
//: cannot leave it describing one station while the ring marks another.
let popupFor = null
//: What was last handed to the source, so the map is left alone when a
//: re-render would set the same thing again.
let drawn = ''
//: Whether the view has been put over the stations. Once only: a reader who
//: has panned somewhere has chosen where they are looking, and a window
//: change is not a reason to take it back.
let fitted = false

onMounted(() => {
  if (canvas.value) {
    start()
  }
})

onBeforeUnmount(() => {
  popup?.remove()
  popup = null
  popupFor = null
  teardownMap?.()
  teardownMap = null
  map = null
})

//: The rows arrive again on every window change, and almost always draw the
//: same map. `refresh` compares before it sets.
watch(collection, refresh)

watch(pickedId, () => {
  paintPicked()
  closeStalePopup()
  reveal()
})

//: A theme flip moves the roles and restyles the basemap under us, and those
//: are two different clocks -- so the colours are re-applied from whichever
//: of them lands second. `styledata` covers the flip; this covers the roles.
watch(roles, paint)

function start() {
  const {map: created, ready, destroy} = createBaseMap(canvas.value, {
    //: A holding view for the moment before the stations are measured, which
    //: `fit` replaces as soon as the source is in.
    center: [20, 10],
    zoom: 2,
  })

  map = created
  teardownMap = destroy

  //: Not swallowed. MapLibre reports a style it could not fetch, a source it
  //: could not tile and a paint expression it could not compile through this
  //: event and through nothing else -- and every one of those leaves a map
  //: that is drawing, just without the stations on it.
  map.on('error', (event) => report(event.error))

  //: Our layers are re-added by the helper's `transformStyle` across a theme
  //: flip, carrying whatever paint they had. Painting again here is what
  //: covers the flip landing before the roles have been re-read.
  map.on('styledata', paint)

  map.on('click', onMapClick)
  map.on('mousemove', onMapMove)

  ready.then(() => {
    try {
      draw()
    } catch (thrown) {
      // Inside the promise, where a throw would otherwise be a rejection
      // nothing listens to: the layers added before it keep rendering and
      // the one that failed is merely absent, which looks like a centre with
      // fewer stations rather than like a bug.
      report(thrown)
    }
  })
}

/** The source and the three layers, once the style is up. */
function draw() {
  map.addSource(SOURCE, {type: 'geojson', data: collection.value})
  drawn = JSON.stringify(collection.value)

  map.addLayer({
    id: LIVE_LAYER,
    type: 'circle',
    source: SOURCE,
    filter: ['!', ['get', 'silent']],
    paint: circlePaint(),
  })

  //: Added after, so it is drawn over: the finding is never underneath.
  map.addLayer({
    id: SILENT_LAYER,
    type: 'circle',
    source: SOURCE,
    filter: ['get', 'silent'],
    paint: circlePaint(),
  })

  map.addLayer({
    id: PICKED_LAYER,
    type: 'circle',
    source: SOURCE,
    filter: filterForPicked(),
    paint: {
      //: A ring and no fill, so the colour underneath it -- which is the
      //: whole of what this map says -- is still readable through it.
      'circle-radius': PICKED_RADIUS,
      'circle-opacity': 0,
      'circle-stroke-width': 2,
    },
  })

  paint()
  fit()
}

/** The parts of a station's paint that do not depend on a resolved colour. */
function circlePaint() {
  return {
    'circle-radius': RADIUS,
    //: Not quite solid, so that where stations do crowd the reader can see
    //: that they are several rather than one.
    'circle-opacity': 0.85,
    'circle-stroke-width': HALO,
  }
}

/**
 * The resolved colours onto the layers.
 *
 * Guarded on every layer rather than on a flag: this runs from a theme watch,
 * from `styledata` and from `draw`, and a style change is a window in which
 * the map is up and our layers are momentarily not.
 */
function paint() {
  if (!map || !roles.value.live) {
    return
  }

  set(LIVE_LAYER, 'circle-color', roles.value.live)
  set(SILENT_LAYER, 'circle-color', roles.value.silent)

  for (const layer of [LIVE_LAYER, SILENT_LAYER]) {
    set(layer, 'circle-stroke-color', roles.value['on-live'])
  }

  set(PICKED_LAYER, 'circle-stroke-color', roles.value.focus)
}

function set(layer, property, value) {
  if (map.getLayer(layer) && value) {
    map.setPaintProperty(layer, property, value)
  }
}

/**
 * Hand the source new stations, but only where they are different.
 *
 * The rows are re-read on every window change and the standings on them do
 * not move with the window, so this is nearly always a no-op -- which is what
 * makes "the map does not redraw when the control changes" a property rather
 * than a promise.
 */
function refresh() {
  const next = JSON.stringify(collection.value)

  if (!map || next === drawn) {
    return
  }

  drawn = next

  const source = map.getSource(SOURCE)

  if (source) {
    source.setData(collection.value)
  }
}

/** The picked layer, filtered to one station or to none. */
function filterForPicked() {
  //: A filter that matches nothing rather than a hidden layer, so there is
  //: one way this ring is off and one way it is on.
  return ['==', ['get', 'id'], pickedId.value ?? -1]
}

/**
 * Take down a popup that is no longer about the picked station.
 *
 * The popup is opened by a click on this map, and the pick it made can be
 * moved from the rows below or cleared there. Left alone, it would sit over
 * the map naming one station while the ring marks another, which is the one
 * way these two surfaces could contradict each other.
 */
function closeStalePopup() {
  if (popup && popupFor !== pickedId.value) {
    popup.remove()
  }
}

function paintPicked() {
  if (map?.getLayer(PICKED_LAYER)) {
    map.setFilter(PICKED_LAYER, filterForPicked())
  }
}

/**
 * Bring the picked station into view, and only where it is not already.
 *
 * A pick made on this map is inside the viewport by definition, so this moves
 * nothing when the reader clicked a dot. A pick made in the table can be
 * anywhere, including off screen, where a ring nobody can see is not a
 * highlight at all.
 */
function reveal() {
  if (!map || pickedId.value === null) {
    return
  }

  const station = byId.value.get(pickedId.value)

  if (!station || typeof station.latitude !== 'number') {
    return
  }

  const at = [station.longitude, station.latitude]

  if (map.getBounds().contains(at)) {
    return
  }

  map.easeTo({center: at})
}

/** The opening view: over the stations, however far apart they are. */
function fit() {
  const bounds = boundsOf(collection.value)

  if (!bounds || fitted) {
    return
  }

  fitted = true

  map.fitBounds(bounds, {
    padding: 40,
    //: A centre with one station would otherwise open at street level, where
    //: a single dot says nothing about where in the world it is.
    maxZoom: 9,
    animate: false,
  })
}

/** Which station is under the pointer, if any. */
function stationAt(point) {
  //: Silent first, because it is the layer on top: a click where the two
  //: overlap picks the one the reader can see.
  const found = map.queryRenderedFeatures(point, {
    layers: [SILENT_LAYER, LIVE_LAYER].filter((layer) => map.getLayer(layer)),
  })

  return found.length ? byId.value.get(found[0].properties.id) : null
}

function onMapClick(event) {
  const station = stationAt(event.point)

  if (!station) {
    //: Clicking the ground clears the pick, which is the gesture a reader
    //: already expects from every map they have used.
    choose('')

    return
  }

  choose(String(station.station_id))
  show(station)
}

function onMapMove(event) {
  map.getCanvas().style.cursor = stationAt(event.point) ? 'pointer' : ''
}

/**
 * What one station is, in full.
 *
 * The popup is where the other two standings live. The surface carries two
 * colours because four do not survive a thousand points, but "transmitting,
 * and nothing declares it" is still a fact about this station, and a reader
 * who has clicked it is asking for exactly that.
 */
function show(station) {
  popup?.remove()
  popupFor = station.station_id

  popup = new maplibregl.Popup({closeButton: true, closeOnClick: false})
      .setLngLat([station.longitude, station.latitude])
      .setHTML(`
        <p class="station-map__popup-name">${asText(displayName(station))}</p>
        <p class="station-map__popup-id">${asText(station.wigos_id)}</p>
        <dl class="station-map__popup-facts">
          <dt>Standing</dt>
          <dd>${asText(STANDING_LABEL[station.standing] || station.standing)}</dd>
          <dt>Last heard</dt>
          <dd>${asText(formatInstant(station.last_heard))}</dd>
          <dt>Quiet</dt>
          <dd>${asText(formatQuiet(station.hours_quiet))}</dd>
        </dl>
      `)
      .addTo(map)

  //: Not chained: `Popup.on` does not hand the popup back, and a chain
  //: through it ends in `undefined.addTo`.
  popup.on('close', () => {
    popup = null
    popupFor = null
  })
}

function choose(station) {
  emit('choose', {station})
}

/** What to call the station: the operator's own name, where there is one. */
function displayName(station) {
  return station.local_name || station.name || station.wigos_id
}

/**
 * A station's own text, as text.
 *
 * Names and ids come out of a registry and out of observed traffic, and this
 * is the one place on the tab that builds markup from them rather than
 * binding it -- MapLibre's popup takes HTML.
 */
function asText(value) {
  const held = document.createElement('span')
  held.textContent = value ?? ''

  return held.innerHTML
}

/** Say on the page what MapLibre would only have said to the console. */
function report(thrown) {
  console.error('The station map:', thrown)

  //: The first one, and then quiet. A source that cannot tile reports on
  //: every attempt, and a panel that grows a line each time is a panel that
  //: pushes the map off the screen.
  if (!failure.value) {
    failure.value = thrown?.message || String(thrown)
  }
}
</script>

<style scoped>
/* Wide enough for a country to have a shape, and no taller: the panel sits in
   a column of charts and the rows below it are what a reader goes to next. */
.station-map__frame {
  position: relative;
  height: 26rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.2rem;
  overflow: hidden;
}

.station-map__canvas {
  position: absolute;
  inset: 0;
}

/* Top-left, the one corner MapLibre's own controls leave alone: navigation
   and fullscreen are bottom-right, attribution bottom-left. */
.station-map__legend {
  position: absolute;
  top: 0.5rem;
  left: 0.5rem;
  z-index: 1;
  padding: 0.4rem 0.6rem;
  border-radius: 0.2rem;
  background: var(--w-color-surface-page);
  border: 1px solid var(--w-color-border-furniture);
  font-size: 0.72rem;
  color: var(--w-color-text-meta);
  max-width: 12rem;
}

.station-map__legend-title {
  margin: 0 0 0.3rem;
  font-weight: 600;
  color: var(--w-color-text-label);
}

.station-map__keys {
  margin: 0;
  padding: 0;
  list-style: none;
}

.station-map__key {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.station-map__swatch {
  display: inline-block;
  width: 0.6rem;
  height: 0.6rem;
  border-radius: 50%;
}

.station-map__swatch--live {
  background: var(--stat-live);
}

.station-map__swatch--silent {
  background: var(--stat-silent);
}

.station-map__legend-note {
  margin: 0.3rem 0 0;
}

.station-map__failure {
  margin-top: 0.5rem;
}

.station-map__unplotted,
.station-map__none {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0.5rem 0 0;
  max-width: 70ch;
}
</style>

<style>
/* Unscoped, because MapLibre builds the popup's own element outside this
   component's tree and a scoped rule would never reach it. Selected under
   the island so these cannot reach the monitoring map's popups, and so that
   the custom properties below resolve: the popup is built inside the map
   container, which is inside `.node-statistics`. */

/* MapLibre's popup ships a white card and near-black text, which on a dark
   admin is a lamp in the corner of the panel. It takes the page's own
   surface instead -- the tab lays no ground of its own anywhere else
   either. */
.node-statistics .maplibregl-popup-content {
  background: var(--w-color-surface-page);
  color: var(--w-color-text-label);
  padding: 0.6rem 0.75rem;
}

.node-statistics .maplibregl-popup-close-button {
  color: var(--w-color-text-meta);
}

/* The tip is four borders with one of them coloured, and which one depends
   on which way the popup opened. */
.node-statistics .maplibregl-popup-anchor-top .maplibregl-popup-tip,
.node-statistics .maplibregl-popup-anchor-top-left .maplibregl-popup-tip,
.node-statistics .maplibregl-popup-anchor-top-right .maplibregl-popup-tip {
  border-bottom-color: var(--w-color-surface-page);
}

.node-statistics .maplibregl-popup-anchor-bottom .maplibregl-popup-tip,
.node-statistics .maplibregl-popup-anchor-bottom-left .maplibregl-popup-tip,
.node-statistics .maplibregl-popup-anchor-bottom-right .maplibregl-popup-tip {
  border-top-color: var(--w-color-surface-page);
}

.node-statistics .maplibregl-popup-anchor-left .maplibregl-popup-tip {
  border-right-color: var(--w-color-surface-page);
}

.node-statistics .maplibregl-popup-anchor-right .maplibregl-popup-tip {
  border-left-color: var(--w-color-surface-page);
}

.node-statistics .station-map__popup-name {
  margin: 0;
  font-weight: 600;
}

.node-statistics .station-map__popup-id {
  margin: 0 0 0.35rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem;
}

.node-statistics .station-map__popup-facts {
  display: grid;
  grid-template-columns: auto auto;
  gap: 0.1rem 0.6rem;
  margin: 0;
  font-size: 0.75rem;
}

.node-statistics .station-map__popup-facts dt {
  color: var(--w-color-text-meta);
}

.node-statistics .station-map__popup-facts dd {
  margin: 0;
}
</style>
