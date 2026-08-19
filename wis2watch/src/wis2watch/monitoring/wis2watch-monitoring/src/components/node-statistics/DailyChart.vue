<template>
  <div ref="el" class="daily-chart">
    <svg
        :width="width"
        :height="height"
        class="daily-chart__plot"
        tabindex="0"
        role="listbox"
        :aria-label="axisLabel"
        :aria-activedescendant="index === null ? undefined : bucketDomId(index)"
        @pointermove="onPointerMove($event, PAD_LEFT)"
        @pointerleave="clear"
        @focus="onFocus"
        @blur="onBlur"
        @keydown="onKeydown"
    >
      <ChartHatch :id="hatchId"/>

      <text :x="PAD_LEFT - 5" y="9" class="stat-tick" text-anchor="end">{{ yTop }}</text>
      <text :x="PAD_LEFT - 5" :y="plotHeight + 3" class="stat-tick" text-anchor="end">0</text>

      <!-- Full coverage, on the same axis the hourly chart draws it on: the
           two charts above and below each other have to be readable against
           one line, or the day is not the hour's aggregate to a reader. -->
      <line
          v-if="declared > 0"
          :x1="PAD_LEFT"
          :x2="width"
          :y1="y(declared)"
          :y2="y(declared)"
          class="stat-gridline"
      />
      <line :x1="PAD_LEFT" :x2="width" :y1="plotHeight" :y2="plotHeight" class="stat-axis"/>

      <g
          v-for="(day, bucket) in daily"
          :id="bucketDomId(bucket)"
          :key="bucket"
          role="option"
          :aria-selected="index === bucket"
          :aria-label="describe(bucket)"
          :transform="`translate(${PAD_LEFT + band.x(bucket)}, 0)`"
      >
        <!-- A day whose traffic named no station has no station count to
             plot, exactly as an hour of it has not. One mark, one meaning. -->
        <rect
            v-if="isNameless(day)"
            :y="plotHeight - STUB_HEIGHT"
            :width="band.barWidth"
            :height="STUB_HEIGHT"
            :fill="`url(#${hatchId})`"
        />
        <!-- The day in progress: an open bucket. Up the left side, across the
             top, and no right side at all -- the bar is left unclosed rather
             than faded, because a fade is made of contrast and reads as "this
             one collapsed and does not matter", which is the exact false
             alarm the mark exists to prevent. -->
        <path
            v-else-if="isOpen(bucket)"
            :d="openBucketPath(day)"
            class="daily-chart__open"
        />
        <rect
            v-else-if="day.stations > 0"
            :y="y(drawn(day))"
            :width="band.barWidth"
            :height="plotHeight - y(drawn(day))"
            fill="var(--stat-live)"
        />

        <!-- The "so far" tick, drawn for the unfinished day whatever it did
             -- including a day it has published nothing at all in, which is
             the outage a reader has come to the page for. -->
        <line
            v-if="buckets[bucket].partial"
            :x1="band.barWidth"
            :x2="band.barWidth"
            :y1="plotHeight - 6"
            :y2="plotHeight + 3"
            class="daily-chart__edge"
        />

        <rect
            v-if="index === bucket"
            :width="band.barWidth"
            :height="plotHeight"
            class="daily-chart__marker"
            :class="{'daily-chart__marker--focused': focused}"
        />
      </g>

      <text
          v-for="bucket in ticks"
          :key="`tick-${bucket}`"
          :x="tickX(bucket)"
          :y="height - 3"
          class="stat-tick"
          :text-anchor="tickAnchor(bucket)"
      >
        {{ buckets[bucket].partial ? 'today' : formatDay(starts[bucket]) }}
      </text>
    </svg>

    <p class="daily-chart__readout">{{ readout }}</p>
  </div>
</template>

<script setup>
/**
 * One bar per UTC day: how much of the centre reported that day.
 *
 * The same unit and the same axis as the hourly chart -- distinct stations
 * against the declared population -- because this is the aggregate the tab
 * carries its node-wide-outage signal on. A population-level scalar per
 * bucket covers every station of a centre in one viewport, which is what lets
 * the availability matrix later be about individual stripes rather than about
 * whether the whole centre stopped.
 *
 * **The newest bucket is the UTC day in progress.** A series whose newest
 * bucket is yesterday cannot show today's outage, which is the one a reader
 * has come for, so the day is served unfinished and marked rather than
 * withheld. It really is short -- at 09:00 UTC the stations that report
 * around midday have not reported yet -- and the mark is what stops that
 * being read as a collapse every single morning.
 *
 * The mark is geometric, not tonal. Reduced opacity was tried and fails in
 * both themes for the same reason: it fades toward white on light and toward
 * black on dark, and either way it *lowers contrast*, so a genuinely short
 * bar reads as "today collapsed and it doesn't matter". The open bucket --
 * dashed outline, edge tick, no right-hand side -- says unfinished rather
 * than unimportant, and is identical in both themes because it owes nothing
 * to colour.
 */
import {computed, ref, useId} from 'vue'

import ChartHatch from './charts/ChartHatch.vue'
import {
  bandScale,
  formatDay,
  formatDayLong,
  spacedTicks,
  useMeasuredWidth,
  yScale,
} from './charts/plot.js'
import {useBucketHover} from './charts/useBucketHover.js'

const props = defineProps({
  /** The window's axis, as the server drew it: `[{start, partial}]`. */
  buckets: {
    type: Array,
    required: true
  },
  /** One entry per bucket: messages, unattributed, stations, and the ratio. */
  daily: {
    type: Array,
    required: true
  },
  /** How many stations the registry declares: the top of the axis. */
  declared: {
    type: Number,
    required: true
  },
  /** When the server computed this, which is how far the open day has got. */
  asOf: {
    type: String,
    required: true
  },
  height: {
    type: Number,
    default: 140
  },
})

//: Room for the y labels on the left and the day labels underneath, in real
//: pixels, for the reason the hourly chart measures rather than scales.
const PAD_LEFT = 30
const PAD_BOTTOM = 14

//: How tall the station-less mark stands, kept identical to the hourly
//: chart's: the same mark at two sizes is two marks to a reader.
const STUB_HEIGHT = 7

const hatchId = useId()
const focused = ref(false)

const {el, width} = useMeasuredWidth()
const plotWidth = computed(() => Math.max(20, width.value - PAD_LEFT))
const plotHeight = computed(() => props.height - PAD_BOTTOM)

const starts = computed(() => props.buckets.map((bucket) => new Date(bucket.start)))

// The declared population, as on the hourly chart. Two charts on one page
// drawn to two different tops would invite exactly the comparison neither of
// them supports.
const yTop = computed(() => Math.max(1, props.declared))

const drawn = (day) => Math.min(day.stations, yTop.value)
const y = computed(() => yScale(yTop.value, plotHeight.value))
const band = computed(() => bandScale(props.daily.length, plotWidth.value))
const ticks = computed(() => spacedTicks(props.daily.length, plotWidth.value))

const {index, onPointerMove, clear, moveTo, step} = useBucketHover(
    () => props.daily.length,
    () => plotWidth.value
)

const axisLabel = computed(
    () => `Stations reporting per UTC day, over the last ${props.daily.length} days. ` +
        'The newest day is still in progress.'
)

//: Half of the widest label these charts put under a bucket. The newest one
//: is always labelled and always sits against the right edge, so a centred
//: label there would be half outside the plot -- which is how "today" comes
//: out reading "toda".
const LABEL_HALF = 18

/** Where a tick label sits, kept inside the plot at either end. */
function tickX(bucket) {
  const centre = PAD_LEFT + band.value.centre(bucket)

  return Math.min(Math.max(centre, PAD_LEFT), width.value)
}

/** Which way a tick label runs, so that the end ones turn inwards. */
function tickAnchor(bucket) {
  const centre = PAD_LEFT + band.value.centre(bucket)

  if (centre + LABEL_HALF > width.value) {
    return 'end'
  }

  return centre - LABEL_HALF < PAD_LEFT ? 'start' : 'middle'
}

function bucketDomId(bucket) {
  return `${hatchId}-bucket-${bucket}`
}

/** A day that carried traffic but named nobody in it. */
function isNameless(day) {
  return day.stations === 0 && day.unattributed_messages > 0
}

/** A day that is still being counted and has something to draw. */
function isOpen(bucket) {
  return props.buckets[bucket].partial && props.daily[bucket].stations > 0
}

/**
 * The unfinished bar: up the left, across the top, and stopped.
 *
 * The missing right-hand side is the mark. A closed dashed rectangle reads as
 * a bar drawn in a different style; a box left open on the side the axis runs
 * toward reads as one that has not got there yet.
 */
function openBucketPath(day) {
  const top = y.value(drawn(day))

  return `M 0 ${plotHeight.value} L 0 ${top} L ${band.value.barWidth} ${top}`
}

function count(value) {
  return value.toLocaleString()
}

/** How far the day in progress has got, from server truth rather than a clock. */
const asOfHour = computed(() => {
  const at = new Date(props.asOf)

  return `${String(at.getUTCHours()).padStart(2, '0')}:00 UTC`
})

/**
 * One day in words.
 *
 * The readout under the chart and the accessible name of the bucket are the
 * same sentence, so a reader listening to the chart and a reader pointing at
 * it are told the same thing -- including which of the two hatched cases a
 * stub is, which the mark itself cannot say.
 */
function describe(bucket) {
  const day = props.daily[bucket]
  const partial = props.buckets[bucket].partial
  const on = partial
      ? `Today so far, to ${asOfHour.value}`
      : formatDayLong(starts.value[bucket])

  if (isNameless(day)) {
    return (
        `${on}: no station reported, but ${count(day.unattributed_messages)} ` +
        `messages arrived carrying no WIGOS identifier.`
    )
  }

  if (day.stations === 0) {
    return `${on}: silent. This centre published nothing.`
  }

  const reporting = props.declared > 0
      ? `${count(day.stations)} of ${count(props.declared)} stations reported`
      : `${count(day.stations)} stations reported`
  const each = day.messages_per_active_station === null
      ? ''
      : `, ${count(day.messages_per_active_station)} each`

  return (
      `${on}: ${reporting}, ${count(day.messages)} messages${each}.` +
      (partial ? ' The day is still being counted.' : '')
  )
}

// What the panel says when the reader is not on a bucket. The window is
// rolling, so which days these are is not something the page states anywhere
// else.
const readout = computed(() => {
  if (index.value !== null) {
    return describe(index.value)
  }

  if (!starts.value.length) {
    return ''
  }

  return (
      `${formatDayLong(starts.value[0])} to today, in whole UTC days. ` +
      `Today is open on its right: it is still being counted, to ${asOfHour.value}.`
  )
})

// Entering the chart lands on today, which is the day an operator checking on
// a centre has come for.
function onFocus() {
  focused.value = true

  if (index.value === null) {
    moveTo(props.daily.length - 1)
  }
}

function onBlur() {
  focused.value = false
  clear()
}

function onKeydown(event) {
  const last = props.daily.length - 1

  if (event.key === 'ArrowRight') {
    step(1)
  } else if (event.key === 'ArrowLeft') {
    step(-1)
  } else if (event.key === 'Home') {
    moveTo(0)
  } else if (event.key === 'End') {
    moveTo(last)
  } else if (event.key === 'Escape') {
    clear()
  } else {
    return
  }

  event.preventDefault()
}
</script>

<style scoped>
.daily-chart__plot {
  display: block;
  width: 100%;
}

.daily-chart__plot:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 2px;
}

/* The unfinished day. Full-strength colour on purpose: the mark is the shape,
   and anything tonal here would be read as a smaller value. */
.daily-chart__open {
  fill: none;
  stroke: var(--stat-live);
  stroke-width: 1.2;
  stroke-dasharray: 2 2;
}

.daily-chart__edge {
  stroke: var(--stat-live);
  stroke-width: 1.4;
}

.daily-chart__marker {
  fill: var(--stat-hover);
  opacity: 0.08;
}

.daily-chart__marker--focused {
  stroke: var(--stat-focus);
  stroke-width: 1;
  opacity: 0.22;
}

/* Anchored under the axis rather than floating over the bars, for the reason
   the hourly chart's is: a card covers a third of a plot this short. */
.daily-chart__readout {
  margin: 0.35rem 0 0;
  min-height: 2.4em;
  font-size: 0.75rem;
  line-height: 1.2;
  color: var(--stat-ink-muted);
}
</style>
