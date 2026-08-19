<template>
  <div ref="host" class="wall">
    <canvas ref="surface" class="wall__canvas" role="img" :aria-label="label"/>
  </div>
</template>

<script setup>
/**
 * The whole population at once, half a pixel a station, above the rows.
 *
 * The matrix beneath this can only ever have about fifty rows on screen. That
 * is the price of a legible row, and #48 settled that the answer to scale is a
 * *second view* rather than a shorter row -- so this is that view: every
 * station in the list, one thin line each, no scrolling, no labels, no
 * virtualisation. At 1000 stations over 90 days it draws a node-wide outage as
 * an unmistakable full-height gap, the decay staircase around it, and the
 * correlated band under that, all in one viewport.
 *
 * **It is a navigator, not a second matrix.** It says "there is something at
 * this end of the population, on these days"; the reader then goes to the rows
 * for which stations and when. Nothing here is clickable and nothing is
 * labelled, because a line half a pixel tall cannot carry a station's name and
 * a gesture that misses by one row would name the wrong one.
 *
 * **It honours the table's filter**, which is the one thing #66 found wrong
 * with the prototype: a wall drawing 1000 stations above a table filtered to
 * 47 is two answers to one question. It is handed the rows the table is
 * showing, in the order the table is showing them, so a band here and a band
 * in the matrix are the same band.
 *
 * **Canvas, and therefore the one surface that resolves the roles.** A
 * thousand stations by ninety buckets is ninety thousand marks, which is a DOM
 * nobody should build. The price is that a theme flip does not repaint what is
 * already painted, so the roles are resolved to strings and the wall is drawn
 * again when they move -- `useRoles` is that seam, and it is the whole of the
 * theme handling here.
 */
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'

import {useRoles} from './charts/useRoles.js'
import {grainOf, pixelBand, presenceStates} from './presence.js'

const props = defineProps({
  /** The rows the table is showing, in the order it is showing them. */
  stations: {
    type: Array,
    required: true
  },
  /** The window's axis, the same one every row's cells are drawn against. */
  buckets: {
    type: Array,
    required: true
  },
  /** The size of one bucket: `day` or `hour`, the server's own spelling. */
  grain: {
    type: String,
    required: true
  },
  /**
   * The most each bucket could have carried, or null for "each row against
   * its own busiest bucket". The table's own, worked out once there rather
   * than again here: a wall judging a bucket by a different ceiling than the
   * cells below it would draw a band that is in no row of the table.
   */
  ceilings: {
    type: Array,
    default: null
  },
  /** What the window is called, for the label a screen reader is given. */
  windowLabel: {
    type: String,
    default: 'the window'
  },
})

//: How much of the wall one station gets. Half a pixel is #48's figure and it
//: is the point of the thing rather than a detail: it is what puts a thousand
//: stations in one viewport, and it is far too little to label -- which is why
//: this is a navigator and the table is the view.
const PX_PER_STATION = 0.5

//: The wall's floor and ceiling in height. The floor is for the small centre,
//: where twenty stations at half a pixel would be a ten-pixel smear rather
//: than a picture; the ceiling is what keeps the promise of "no scrolling"
//: at a centre big enough to break it, at the price of squeezing the rows
//: below half a pixel -- which the painting below is written to survive.
const MIN_HEIGHT = 44
const MAX_HEIGHT = 500

//: Which role paints which state, and in which order. A map rather than a
//: branch, in the same shape as the matrix's own stylesheet and naming the
//: same three roles, so that the wall and the cells beneath it are one
//: legend rather than two.
//:
//: The *order* is the whole trick of drawing a thousand rows into five
//: hundred pixels. Where two stations share a pixel, the one painted last is
//: the one seen -- so the worse news is painted last, and an outage cannot be
//: hidden by a healthy neighbour it happens to be squeezed against. A wall
//: that lost a dark station to rounding would be a navigator pointing at
//: nothing.
const PAINT_ORDER = [
  {state: 'full', role: 'live'},
  {state: 'thin', role: 'thin'},
  {state: 'silent', role: 'empty'},
]

const host = ref(null)
const surface = ref(null)

// The roles this wall paints with, and every one of them is also a fill in
// the matrix's own stylesheet. The ground is `empty` rather than the panel's
// background on purpose: the wall's ground *is* silence, and what a reader
// hunts on it is the absence of the live colour.
const roles = useRoles(host, ['live', 'thin', 'empty'])

const width = ref(0)

const height = computed(() => Math.max(
    MIN_HEIGHT,
    Math.min(MAX_HEIGHT, Math.round(props.stations.length * PX_PER_STATION))
))

const label = computed(() => {
  const period = grainOf(props.grain).period

  return (
      `All ${props.stations.length.toLocaleString()} stations listed below at`
      + ` once, one line each, in the same order and the same colours as the`
      + ` rows: the ${period} of ${props.windowLabel.toLowerCase()} run left to`
      + ` right, oldest first. A gap the full height of it is every station`
      + ` silent on the same ${props.grain}. The rows below carry the same`
      + ` finding station by station, with names and figures.`
  )
})

/**
 * Draw the wall.
 *
 * By state rather than by row: one `fillStyle` per colour instead of one per
 * cell, and each row's run of same-state buckets drawn as a single rectangle.
 * What that turns ninety thousand fills into is a few thousand, because real
 * presence is runs -- a station is heard for weeks and then is not.
 */
function paint() {
  const canvas = surface.value

  if (!canvas || !width.value || !props.stations.length || !props.buckets.length) {
    return
  }

  // Device pixels, and the drawing is done in them rather than in CSS pixels
  // with a scale on the context. The bands are already rounded to whole
  // pixels to keep every station visible, and rounding in the wrong unit
  // would hand that back on any display that is not at 1x.
  const ratio = window.devicePixelRatio || 1
  const canvasWidth = Math.max(1, Math.round(width.value * ratio))
  const canvasHeight = Math.max(1, Math.round(height.value * ratio))

  canvas.width = canvasWidth
  canvas.height = canvasHeight
  canvas.style.width = `${width.value}px`
  canvas.style.height = `${height.value}px`

  const context = canvas.getContext('2d')

  context.fillStyle = roles.value.empty
  context.fillRect(0, 0, canvasWidth, canvasHeight)

  const columns = props.buckets.map((_, at) => pixelBand(at, props.buckets.length, canvasWidth))
  const rows = props.stations.map(
      (station) => presenceStates(station.presence || [], props.ceilings, props.buckets.length)
  )

  PAINT_ORDER.forEach(({state, role}) => {
    context.fillStyle = roles.value[role]

    rows.forEach((states, at) => {
      const [top, bottom] = pixelBand(at, rows.length, canvasHeight)

      // The run rather than the cell: a station heard through a whole window
      // is one rectangle, and it is the ordinary case.
      let from = -1

      states.forEach((cell, column) => {
        if (cell === state && from === -1) {
          from = column
        }

        const ends = cell !== state || column === states.length - 1

        if (from !== -1 && ends) {
          const last = cell === state ? column : column - 1

          context.fillRect(
              columns[from][0],
              top,
              columns[last][1] - columns[from][0],
              bottom - top
          )

          from = -1
        }
      })
    })
  })
}

//: Painted at most once a frame. Every one of the things this watches can
//: move in the same tick -- a filter changes the rows and the height with
//: them -- and a wall that repainted per prop would draw a thousand rows
//: three times over for one gesture.
let queued = false

function repaint() {
  if (queued) {
    return
  }

  queued = true
  requestAnimationFrame(() => {
    queued = false
    paint()
  })
}

let observer = null

onMounted(() => {
  // The panel is inside Wagtail's own layout, so the wall's width is whatever
  // the page leaves it rather than a number this component can know. Watched
  // rather than read once: the admin's sidebar collapses.
  observer = new ResizeObserver(() => {
    width.value = host.value?.clientWidth || 0
  })

  if (host.value) {
    observer.observe(host.value)
    width.value = host.value.clientWidth
  }
})

onBeforeUnmount(() => observer?.disconnect())

watch(
    [width, height, roles, () => props.stations, () => props.buckets, () => props.ceilings],
    repaint
)
</script>

<style scoped>
/* The panel's own furniture, in Wagtail's colours: the wall is a mark, and
   the box around it is not. */
.wall {
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.2rem;
  overflow: hidden;
  margin-bottom: 0.5rem;
}

.wall__canvas {
  display: block;
}
</style>
