<template>
  <div ref="el" class="hourly-chart">
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
      <ChartHatch :id="hatchId"/>

      <text :x="PAD_LEFT - 5" y="9" class="stat-tick" text-anchor="end">{{ yTop }}</text>
      <text :x="PAD_LEFT - 5" :y="plotHeight + 3" class="stat-tick" text-anchor="end">0</text>

      <!-- Where full coverage is. The bars are read against this line rather
           than against each other, which is the whole reason the axis top is
           the declared population and not the tallest hour. -->
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
          v-for="(hour, bucket) in hourly"
          :id="bucketDomId(bucket)"
          :key="bucket"
          role="option"
          :aria-selected="index === bucket"
          :aria-label="describe(bucket)"
          :transform="`translate(${PAD_LEFT + band.x(bucket)}, 0)`"
      >
        <!-- An hour whose traffic named no station has no station count to
             plot, and is not a silent hour either. It gets a mark that claims
             no height on an axis it does not belong to. -->
        <rect
            v-if="isNameless(hour)"
            :y="plotHeight - STUB_HEIGHT"
            :width="band.barWidth"
            :height="STUB_HEIGHT"
            :fill="`url(#${hatchId})`"
        />
        <rect
            v-else-if="hour.stations > 0"
            :y="y(drawn(hour))"
            :width="band.barWidth"
            :height="plotHeight - y(drawn(hour))"
            fill="var(--stat-live)"
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
          :x="PAD_LEFT + band.centre(bucket)"
          :y="height - 3"
          class="stat-tick"
          text-anchor="middle"
      >
        {{ formatHour(starts[bucket]) }}
      </text>
    </svg>

    <p class="stat-readout">{{ readout }}</p>
  </div>
</template>

<script setup>
/**
 * The last 24 whole UTC hours, as one bar per hour.
 *
 * **The bars are distinct stations, not messages**, and that is the decision
 * everything else here follows from. The y top is the declared population, so
 * the height of a bar *is* coverage -- the only reading under which this
 * chart says anything about the centre rather than about its busiest topic --
 * and two readers screenshotting the same node get one axis. The cost is
 * accepted: a single hour where one station dropped out draws as a notch
 * rather than as a cliff, and the availability matrix carries that case.
 *
 * Message volume is never on this axis. It is in the words below the chart,
 * because a second unit on a shared axis is how a reader ends up comparing a
 * message count to a station count without noticing.
 *
 * That unit is what forces the mark. An hour where the centre published
 * messages that named no station at all is not a silent hour, but it has no
 * station count to plot -- so it draws as a hatched stub on the baseline,
 * plainly distinct from an hour that drew nothing, and the readout says in
 * words which of the two it is. There is no pseudo-row for it anywhere.
 *
 * The hour in progress is not here. The server ends the window at the last
 * whole hour, so the newest bar is a finished count rather than a partial one
 * that would draw as a collapse every time the page is opened.
 */
import {computed, useId} from 'vue'

import ChartHatch from './charts/ChartHatch.vue'
import {
  PAD_BOTTOM,
  PAD_LEFT,
  STUB_HEIGHT,
  bandScale,
  clockTicks,
  formatCount,
  formatHour,
  formatHourLong,
  useMeasuredWidth,
  yScale,
} from './charts/plot.js'
import {useBucketHover} from './charts/useBucketHover.js'

const props = defineProps({
  /** The hourly axis, as the server drew it: `[{start, partial}]`. */
  buckets: {
    type: Array,
    required: true
  },
  /** One entry per bucket: messages, unattributed messages, distinct stations. */
  hourly: {
    type: Array,
    required: true
  },
  /** How many stations the registry declares: the top of the axis. */
  declared: {
    type: Number,
    required: true
  },
  height: {
    type: Number,
    default: 140
  },
})

const hatchId = useId()

const {el, width} = useMeasuredWidth()
const plotWidth = computed(() => Math.max(20, width.value - PAD_LEFT))
const plotHeight = computed(() => props.height - PAD_BOTTOM)

const starts = computed(() => props.buckets.map((bucket) => new Date(bucket.start)))

// The declared population, and not the tallest bar. Height is coverage only
// if the top never moves: two readers screenshotting one node on two days
// have to be able to lay the images side by side. The floor of 1 is for a
// centre that declares nothing, which still gets a real axis to draw zero
// against.
const yTop = computed(() => Math.max(1, props.declared))

// A centre transmitting for more stations than anything declares reports over
// full coverage, and that hour draws at the top rather than off the panel.
// The bar is capped; the words below it are not, and say "43 of 40".
const drawn = (hour) => Math.min(hour.stations, yTop.value)
const y = computed(() => yScale(yTop.value, plotHeight.value))
const band = computed(() => bandScale(props.hourly.length, plotWidth.value))
const ticks = computed(() =>
    clockTicks(starts.value.map((start) => start.getUTCHours()), plotWidth.value)
)

// Focus and the arrow keys come from the same composable the pointer does,
// so a reader walking this chart by keyboard and a reader pointing at it are
// on one bucket -- and every chart on the tab answers the same keys.
const {index, focused, onPointerMove, clear, onFocus, onBlur, onKeydown} = useBucketHover(
    () => props.hourly.length,
    () => plotWidth.value
)

const axisLabel = computed(
    () => `Stations reporting per hour, over the last ${props.hourly.length} whole UTC hours`
)

function bucketDomId(bucket) {
  return `${hatchId}-bucket-${bucket}`
}

/** An hour that carried traffic but named nobody in it. */
function isNameless(hour) {
  return hour.stations === 0 && hour.unattributed_messages > 0
}

/**
 * One hour in words.
 *
 * The same sentence serves the readout under the chart and the accessible
 * name of the bucket, so a reader listening to the chart and a reader
 * pointing at it are told the same thing. Which is also the only place the
 * two hatched cases are told apart: the mark can say "no value on this axis"
 * and no more.
 */
function describe(bucket) {
  const hour = props.hourly[bucket]
  const at = formatHourLong(starts.value[bucket])

  if (isNameless(hour)) {
    return (
        `${at}: no station reported, but ${formatCount(hour.unattributed_messages)} ` +
        `messages arrived carrying no WIGOS identifier.`
    )
  }

  if (hour.stations === 0) {
    return `${at}: silent. This centre published nothing in this hour.`
  }

  const reporting = props.declared > 0
      ? `${formatCount(hour.stations)} of ${formatCount(props.declared)} stations reported`
      : `${formatCount(hour.stations)} stations reported`
  const nameless = hour.unattributed_messages > 0
      ? `, ${formatCount(hour.unattributed_messages)} of them naming no station`
      : ''

  return `${at}: ${reporting}, ${formatCount(hour.messages)} messages${nameless}.`
}

// What the panel says when the reader is not on a bucket: which hours these
// are. The window is rolling, so "the last 24 hours" is not a date anybody
// can work out from the page.
const readout = computed(() => {
  if (index.value !== null) {
    return describe(index.value)
  }

  if (!starts.value.length) {
    return ''
  }

  return (
      `${formatHourLong(starts.value[0])} to ` +
      `${formatHourLong(starts.value[starts.value.length - 1])}, in whole UTC hours.`
  )
})

</script>
