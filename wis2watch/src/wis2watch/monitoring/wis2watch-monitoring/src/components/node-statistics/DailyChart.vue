<template>
  <div ref="el" class="daily-chart">
    <svg
        :width="width"
        :height="height"
        class="stat-plot"
        :class="{'stat-plot--selectable': selectable}"
        tabindex="0"
        role="listbox"
        :aria-label="axisLabel"
        :aria-activedescendant="index === null ? undefined : bucketDomId(index)"
        @pointermove="onPointerMove($event, PAD_LEFT)"
        @click="onClick($event, PAD_LEFT)"
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
          :aria-selected="chosen === bucket"
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
            class="stat-open"
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
             the outage a reader has come to the page for, and a day whose
             traffic named nobody, where the hatch has the height and there is
             no bar left to leave open. The tick is the part of the mark that
             does not need one. -->
        <line
            v-if="buckets[bucket].partial"
            :x1="band.barWidth"
            :x2="band.barWidth"
            :y1="plotHeight - 6"
            :y2="plotHeight + 3"
            class="stat-edge"
        />

        <!-- The bucket the reader picked, and it stays there while the
             pointer moves on: the hover mark says where the reader is, and
             this says what the table below is filtered by. Outlined rather
             than washed, so the two are told apart at a glance. -->
        <rect
            v-if="chosen === bucket"
            :width="band.barWidth"
            :height="plotHeight"
            class="stat-chosen"
        />

        <rect
            v-if="index === bucket"
            :width="band.barWidth"
            :height="plotHeight"
            class="stat-marker"
            :class="{'stat-marker--focused': focused}"
        />
      </g>

      <text
          v-for="bucket in ticks"
          :key="`tick-${bucket}`"
          :x="tick(bucket).x"
          :y="height - 3"
          class="stat-tick"
          :text-anchor="tick(bucket).anchor"
      >
        {{ buckets[bucket].partial ? 'today' : formatDay(starts[bucket]) }}
      </text>
    </svg>

    <p class="stat-readout">{{ readout }}</p>
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
import {computed, useId} from 'vue'

import ChartHatch from './charts/ChartHatch.vue'
import {
  PAD_BOTTOM,
  PAD_LEFT,
  STUB_HEIGHT,
  bandScale,
  formatCount,
  formatDay,
  formatDayLong,
  spacedTicks,
  tickPlacement,
  useMeasuredWidth,
  yScale,
} from './charts/plot.js'
import {useBucketHover} from './charts/useBucketHover.js'
import {SELECT_HINT, bucketIndexOf} from './selection.js'

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
  /**
   * The bucket the reader has picked, as the server spelled its start, or
   * empty for none. A start rather than an index, because this chart's axis
   * and the station rows' axis arrive on two separate requests: an index
   * means "the third column of whichever list you happen to be holding".
   */
  selected: {
    type: String,
    default: ''
  },
  /**
   * Whether picking a bucket here means anything. False where this chart's
   * axis is not the one the station rows are drawn against, because a click
   * naming a bucket the matrix has no column for would filter the table to
   * nothing at all.
   */
  selectable: {
    type: Boolean,
    default: false
  },
  height: {
    type: Number,
    default: 140
  },
})

const emit = defineEmits(['select'])

/**
 * Say which bucket the reader picked, or that they dropped the selection.
 *
 * The bucket's start rather than its index, which is the one thing this chart
 * and the station rows can both be sure means the same bucket.
 */
function pick(at) {
  if (!props.selectable) {
    return
  }

  // Picking the bucket that is already picked drops it, so the gesture that
  // made the filter is also the one that undoes it -- the pointer's way out,
  // beside Escape's.
  const start = at === null ? '' : props.buckets[at].start

  emit('select', start === props.selected ? '' : start)
}

//: Where the selection sits on this axis, or -1 where it names no bucket of
//: it -- a link carrying a day of a 90-day window, opened at 24 hours.
const chosen = computed(() => bucketIndexOf(props.buckets, props.selected))

const hatchId = useId()

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

// Focus and the arrow keys come from the same composable the pointer does,
// so a reader walking this chart by keyboard and a reader pointing at it are
// on one bucket -- and every chart on the tab answers the same keys.
const {index, focused, onPointerMove, onClick, clear, onFocus, onBlur, onKeydown} =
    useBucketHover(
        () => props.daily.length,
        () => plotWidth.value,
        {onSelect: pick}
    )

const axisLabel = computed(
    () => `Stations reporting per UTC day, over the last ${props.daily.length} days. ` +
        `The newest day is still in progress.${props.selectable ? ` ${SELECT_HINT}` : ''}`
)

/** Where one tick label goes, turned inwards at the ends of the axis. */
function tick(bucket) {
  return tickPlacement(PAD_LEFT + band.value.centre(bucket), PAD_LEFT, width.value)
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

/**
 * How far the day in progress has got.
 *
 * From ``generated_at`` rather than from a laptop clock -- and rather than
 * from the window's ``until``, which the contract names but which at daily
 * grain is the *end* of the day in progress, tomorrow midnight, and so says
 * nothing about how much of today has been counted.
 */
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
        `${on}: no station reported, but ${formatCount(day.unattributed_messages)} ` +
        `messages arrived carrying no WIGOS identifier.`
    )
  }

  if (day.stations === 0) {
    return `${on}: silent. This centre published nothing.`
  }

  const reporting = props.declared > 0
      ? `${formatCount(day.stations)} of ${formatCount(props.declared)} stations reported`
      : `${formatCount(day.stations)} stations reported`
  const each = day.messages_per_active_station === null
      ? ''
      : `, ${formatCount(day.messages_per_active_station)} each`

  return (
      `${on}: ${reporting}, ${formatCount(day.messages)} messages${each}.` +
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

</script>
