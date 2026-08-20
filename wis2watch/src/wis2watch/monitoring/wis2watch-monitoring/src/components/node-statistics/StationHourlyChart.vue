<template>
  <div ref="el" class="station-hourly">
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

      <text :x="PAD_LEFT - 5" y="9" class="stat-tick" text-anchor="end">
        {{ compactCount(yTop) }}
      </text>
      <text :x="PAD_LEFT - 5" :y="plotHeight + 3" class="stat-tick" text-anchor="end">0</text>

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
        <!-- An hour the centre published in and named nobody in. This station
             has no count to plot there and was not necessarily silent, so it
             gets the mark the aggregate chart gives the same hour rather than
             a bar of zero -- which would blame the station for its centre's
             attribution gap. -->
        <rect
            v-if="isNameless(hour)"
            :y="plotHeight - STUB_HEIGHT"
            :width="band.barWidth"
            :height="STUB_HEIGHT"
            :fill="`url(#${hatchId})`"
        />
        <rect
            v-else-if="hour.messages > 0"
            :y="y(hour.messages)"
            :width="band.barWidth"
            :height="plotHeight - y(hour.messages)"
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
 * One station's last 24 whole UTC hours, as one bar per hour.
 *
 * **The bars are messages**, which is the one place this chart parts company
 * with the aggregate above it. Up there the unit is distinct stations, because
 * the question is how much of a centre is reporting; here there is one station
 * and the only thing left to count is how much it published. So the axis top
 * is a rounded number rather than a population, and it is the station's own
 * busiest hour that sets it -- station traffic is heavy-tailed, and there is
 * no coverage ceiling a single station's volume could be read against.
 *
 * That change of unit is why this is a component rather than the aggregate
 * chart handed different data: a bar meaning "3 messages" and a bar meaning
 * "3 stations" cannot be the same drawing with a different label, and the
 * arithmetic they share is in `charts/plot.js` where it belongs.
 *
 * **An hour of nothing draws as nothing, on a real axis.** A station with no
 * traffic at all gets the axis, the baseline and 24 empty bands rather than an
 * empty panel: silence is the finding this page is opened for, and a blank
 * rectangle says "no data" instead.
 *
 * No pick gesture. A bucket picked on the tab's charts filters the station
 * table below to what was dark in it, and there is nothing on this page to
 * filter -- so the hover and keyboard routes are here and the selection is
 * not, rather than offering a gesture that would do nothing.
 */
import {computed, useId} from 'vue'

import ChartHatch from './charts/ChartHatch.vue'
import {
  PAD_BOTTOM,
  PAD_LEFT,
  STUB_HEIGHT,
  bandScale,
  clockTicks,
  compactCount,
  formatCount,
  formatHour,
  formatHourLong,
  niceTop,
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
  /** One entry per bucket: this station's messages, and the centre's silence. */
  hourly: {
    type: Array,
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

// Rounded up to a number a reader can find a middle of by eye. There is no
// population to top this axis out at, so the alternative is an axis labelled
// with whatever the busiest hour happened to be.
const yTop = computed(() =>
    niceTop(Math.max(0, ...props.hourly.map((hour) => hour.messages)))
)

const y = computed(() => yScale(yTop.value, plotHeight.value))
const band = computed(() => bandScale(props.hourly.length, plotWidth.value))
const ticks = computed(() =>
    clockTicks(starts.value.map((start) => start.getUTCHours()), plotWidth.value)
)

// The hover layer and the keyboard route, from the same composable every
// other surface on the tab uses, so a reader who has learned the arrow keys
// on the charts above has learned them here.
const {index, focused, onPointerMove, clear, onFocus, onBlur, onKeydown} =
    useBucketHover(() => props.hourly.length, () => plotWidth.value)

const axisLabel = computed(
    () => `Messages published for this station per hour, over the last ` +
        `${props.hourly.length} whole UTC hours.`
)

function bucketDomId(bucket) {
  return `${hatchId}-bucket-${bucket}`
}

/** An hour the centre published in and named nobody in. */
function isNameless(hour) {
  return hour.messages === 0 && hour.station_less
}

/**
 * One hour in words.
 *
 * The same sentence serves the readout under the chart and the accessible
 * name of the bucket, and it is the only place the two empty cases are told
 * apart: a mark can say "no value on this axis" and no more.
 */
function describe(bucket) {
  const hour = props.hourly[bucket]
  const at = formatHourLong(starts.value[bucket])

  if (isNameless(hour)) {
    return (
        `${at}: nothing was published for this station, and what this centre ` +
        `did publish in this hour named no station at all.`
    )
  }

  if (hour.messages === 0) {
    return `${at}: silent. Nothing was published for this station.`
  }

  return `${at}: ${formatCount(hour.messages)} messages published for this station.`
}

// What the panel says when the reader is not on a bucket: which hours these
// are. The window is rolling, so "the last 24 hours" is not a date anybody can
// work out from the page.
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
