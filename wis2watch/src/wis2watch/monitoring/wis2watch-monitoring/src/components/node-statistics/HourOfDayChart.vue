<template>
  <div ref="el" class="hour-of-day-chart">
    <svg
        :width="width"
        :height="height"
        class="stat-plot"
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
      <text :x="PAD_LEFT - 5" y="9" class="stat-tick" text-anchor="end">
        {{ compactCount(yTop) }}
      </text>
      <text :x="PAD_LEFT - 5" :y="plotHeight + 3" class="stat-tick" text-anchor="end">0</text>

      <!-- What an even day would look like: the same volume in every hour.
           The rhythm is the distance from this line, so the line is drawn
           rather than left to be imagined. -->
      <line
          v-if="total > 0"
          :x1="PAD_LEFT"
          :x2="width"
          :y1="y(total / HOURS)"
          :y2="y(total / HOURS)"
          class="stat-gridline"
      />
      <line :x1="PAD_LEFT" :x2="width" :y1="plotHeight" :y2="plotHeight" class="stat-axis"/>

      <g
          v-for="(messages, hour) in hourOfDay"
          :id="bucketDomId(hour)"
          :key="hour"
          role="option"
          :aria-selected="index === hour"
          :aria-label="describe(hour)"
          :transform="`translate(${PAD_LEFT + band.x(hour)}, 0)`"
      >
        <rect
            v-if="messages > 0"
            :y="y(messages)"
            :width="band.barWidth"
            :height="plotHeight - y(messages)"
            fill="var(--stat-live)"
        />

        <rect
            v-if="index === hour"
            :width="band.barWidth"
            :height="plotHeight"
            class="stat-marker"
            :class="{'stat-marker--focused': focused}"
        />
      </g>

      <text
          v-for="hour in ticks"
          :key="`tick-${hour}`"
          :x="PAD_LEFT + band.centre(hour)"
          :y="height - 3"
          class="stat-tick"
          text-anchor="middle"
      >
        {{ label(hour) }}
      </text>
    </svg>

    <p class="stat-readout">{{ readout }}</p>
  </div>
</template>

<script setup>
/**
 * Message volume by UTC hour of day, summed over the whole window.
 *
 * **The one chart on the tab in raw message volume**, and that is deliberate.
 * The hourly chart gave the synoptic rhythm up when its unit became distinct
 * stations -- coverage says how much of a centre is reporting and nothing at
 * all about whether it publishes on the synoptic hours -- so the rhythm lives
 * here, on an axis of its own, in a panel that says so.
 *
 * Twenty-four buckets, and they are the *clock* rather than moments on it. A
 * bar is every 06Z in the window added together, which is the only way a
 * habit is visible at all: one day's 06Z is an anecdote, ninety of them are a
 * schedule. Which also means there is no partial bucket here and nothing to
 * mark as unfinished -- the day in progress contributes its hours to the
 * hours it has reached, and no bucket is "still being counted" in a way a
 * reader could act on.
 *
 * The hours are UTC, from the server, and the panel says so in its heading.
 * A local-time fold would move every peak by the reader's own offset and read
 * as a centre that publishes at odd hours.
 */
import {computed, useId} from 'vue'

import {
  bandScale,
  clockTicks,
  compactCount,
  niceTop,
  useMeasuredWidth,
  yScale,
} from './charts/plot.js'
import {useBucketHover} from './charts/useBucketHover.js'

const props = defineProps({
  /** 24 message counts, indexed by UTC hour, as the server folded them. */
  hourOfDay: {
    type: Array,
    required: true
  },
  /** The window these hours were summed over, for the words under the axis. */
  windowLabel: {
    type: String,
    required: true
  },
  height: {
    type: Number,
    default: 120
  },
})

//: The same furniture every panel on the tab leaves, so the plots line up.
const PAD_LEFT = 30
const PAD_BOTTOM = 14

//: How many buckets the clock has. The server sends exactly this many.
const HOURS = 24

const hourId = useId()

const {el, width} = useMeasuredWidth()
const plotWidth = computed(() => Math.max(20, width.value - PAD_LEFT))
const plotHeight = computed(() => props.height - PAD_BOTTOM)

const total = computed(() => props.hourOfDay.reduce((sum, messages) => sum + messages, 0))

// Rounded up from the busiest hour. Volume has no ceiling the way coverage
// has a declared population, so there is no fixed top for this to mean.
const yTop = computed(() => niceTop(Math.max(...props.hourOfDay, 0)))

const y = computed(() => yScale(yTop.value, plotHeight.value))
const band = computed(() => bandScale(props.hourOfDay.length, plotWidth.value))

// Labelled by the same arithmetic the hourly chart is, so 06Z is spaced the
// same on both panels and a reader learns one axis rather than two.
const ticks = computed(() =>
    clockTicks(
        props.hourOfDay.map((_messages, hour) => hour),
        plotWidth.value
    )
)

const {index, focused, onPointerMove, clear, onFocus, onBlur, onKeydown} = useBucketHover(
    () => props.hourOfDay.length,
    () => plotWidth.value
)

const axisLabel = computed(
    () => 'Messages by UTC hour of day, summed over ' +
        `${props.windowLabel.toLowerCase()}.`
)

function label(hour) {
  return `${String(hour).padStart(2, '0')}Z`
}

function bucketDomId(hour) {
  return `${hourId}-hour-${hour}`
}

function count(value) {
  return value.toLocaleString()
}

//: The busiest hour of the clock, which is the finding a reader is looking
//: for and is not always the tallest bar they can see on a narrow panel.
const peak = computed(() =>
    props.hourOfDay.reduce(
        (best, messages, hour) => (messages > props.hourOfDay[best] ? hour : best),
        0
    )
)

/**
 * One hour of the clock in words.
 *
 * The share is what makes a bar mean something: 14% of a window's traffic in
 * one hour of twenty-four is a schedule, and 4% is an even day. The same
 * sentence serves the readout and the accessible name of the bucket.
 */
function describe(hour) {
  const messages = props.hourOfDay[hour]

  if (!messages) {
    return `${label(hour)}: nothing published in this hour, on any day of the window.`
  }

  const share = total.value > 0 ? Math.round((messages / total.value) * 100) : 0

  return (
      `${label(hour)}: ${count(messages)} messages, ${share}% of everything ` +
      'this centre published in the window.'
  )
}

// What the panel says when the reader is not on a bar: what the bars are made
// of, and where the peak is -- which is the whole reading of this chart.
const readout = computed(() => {
  if (index.value !== null) {
    return describe(index.value)
  }

  if (!total.value) {
    return `Nothing was published in ${props.windowLabel.toLowerCase()}.`
  }

  return (
      `Every ${label(peak.value)} of ${props.windowLabel.toLowerCase()} added ` +
      `together is this centre's busiest hour, at ${count(props.hourOfDay[peak.value])} ` +
      'messages. The dashed line is what an even day would be.'
  )
})

</script>
