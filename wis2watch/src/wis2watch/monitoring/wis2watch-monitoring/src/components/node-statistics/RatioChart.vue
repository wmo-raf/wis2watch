<template>
  <div ref="el" class="ratio-chart">
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

      <text :x="PAD_LEFT - 5" y="9" class="stat-tick" text-anchor="end">
        {{ compactCount(yTop) }}
      </text>
      <text :x="PAD_LEFT - 5" :y="plotHeight + 3" class="stat-tick" text-anchor="end">0</text>

      <line :x1="PAD_LEFT" :x2="width" :y1="plotHeight" :y2="plotHeight" class="stat-axis"/>

      <g :transform="`translate(${PAD_LEFT}, 0)`">
        <!-- A day with no active station has no value on this axis at all --
             not a low one and not a zero -- so the line stops, and the hatch
             stands where it would have been. The same mark the hourly chart
             puts under a station-less hour, meaning the one thing it means. -->
        <rect
            v-for="bucket in hatched"
            :key="`hatch-${bucket}`"
            :x="band.x(bucket)"
            :y="plotHeight - STUB_HEIGHT"
            :width="band.barWidth"
            :height="STUB_HEIGHT"
            :fill="`url(#${hatchId})`"
        />

        <path
            v-for="(run, at) in runs"
            :key="`run-${at}`"
            :d="run"
            class="ratio-chart__line"
        />

        <!-- A day whose neighbours both broke the line has no segment to be
             drawn on, and would otherwise be a value the chart simply does
             not show. -->
        <circle
            v-for="point in isolated"
            :key="`point-${point.bucket}`"
            :cx="point.x"
            :cy="point.y"
            r="1.8"
            class="ratio-chart__point"
        />

        <!-- The day in progress, marked as the daily series marks it: the
             segment into it is left open -- dashed, and stopped at a hollow
             point rather than closed onto a solid one. Its ratio is a partial
             day's messages over a partial day's stations, which is a real
             figure that is not yet the day's. -->
        <path v-if="openRun" :d="openRun" class="stat-open"/>
        <circle
            v-if="openPoint"
            :cx="openPoint.x"
            :cy="openPoint.y"
            r="2.2"
            class="stat-edge"
        />

        <!-- The "so far" tick, drawn for the unfinished day whatever it did,
             including a day that has no ratio at all. -->
        <line
            v-if="partialBucket !== null"
            :x1="band.x(partialBucket) + band.barWidth"
            :x2="band.x(partialBucket) + band.barWidth"
            :y1="plotHeight - 6"
            :y2="plotHeight + 3"
            class="stat-edge"
        />
      </g>

      <g
          v-for="(day, bucket) in daily"
          :id="bucketDomId(bucket)"
          :key="bucket"
          role="option"
          :aria-selected="chosen === bucket"
          :aria-label="describe(bucket)"
          :transform="`translate(${PAD_LEFT + band.x(bucket)}, 0)`"
      >
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
 * Messages per active station, one point per UTC day.
 *
 * The ratio the server has already computed, drawn as a line because it is
 * the one series on the tab whose *shape over time* is the finding rather
 * than any bucket's height. A centre whose station count holds steady while
 * this climbs is behaving differently from one where the two move together:
 * the first is publishing more from the same network, the second has simply
 * got bigger.
 *
 * **The null bucket is why this is not a bar chart.** A day on which no
 * station reported has no value on this axis at all -- not zero, which would
 * draw as a floor and read as "each station said nothing". So the line
 * *breaks* and the bucket takes the hatch, which is the tab's one mark for
 * "no value here", already carrying the station-less hour on the chart above.
 * The mark cannot say which of the two cases it is -- a silent day, or a day
 * of traffic that named nobody -- so the words under the axis do, for the
 * pointer and the keyboard alike.
 *
 * The axis top is rounded up from the data rather than fixed, unlike the two
 * coverage charts above: a ratio has no ceiling to be read against, so there
 * is nothing here for a fixed top to mean.
 */
import {computed, useId} from 'vue'

import ChartHatch from './charts/ChartHatch.vue'
import {
  PAD_BOTTOM,
  PAD_LEFT,
  STUB_HEIGHT,
  bandScale,
  compactCount,
  formatCount,
  formatDay,
  formatDayLong,
  niceTop,
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
    default: 120
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

const ratios = computed(() => props.daily.map((day) => day.messages_per_active_station))

// Rounded up from the tallest day, because a ratio has no declared population
// to be read against. The floor of 1 keeps a centre whose every day is null
// on a real axis rather than a degenerate one.
const yTop = computed(() => niceTop(Math.max(...ratios.value.filter(has), 0)))

const y = computed(() => yScale(yTop.value, plotHeight.value))
const band = computed(() => bandScale(props.daily.length, plotWidth.value))
const ticks = computed(() => spacedTicks(props.daily.length, plotWidth.value))

const {index, focused, onPointerMove, onClick, clear, onFocus, onBlur, onKeydown} =
    useBucketHover(
        () => props.daily.length,
        () => plotWidth.value,
        {onSelect: pick}
    )

const axisLabel = computed(
    () => `Messages per active station, per UTC day, over the last ` +
        `${props.daily.length} days. Days on which no station reported have no ` +
        `value and break the line.${props.selectable ? ` ${SELECT_HINT}` : ''}`
)

/** Where one tick label goes, turned inwards at the ends of the axis. */
function tick(bucket) {
  return tickPlacement(PAD_LEFT + band.value.centre(bucket), PAD_LEFT, width.value)
}

function bucketDomId(bucket) {
  return `${hatchId}-bucket-${bucket}`
}

/** A bucket that has a ratio at all. */
function has(ratio) {
  return ratio !== null && ratio !== undefined
}

/** Where one point sits, in plot coordinates. */
function pointAt(bucket) {
  return {
    bucket,
    x: band.value.centre(bucket),
    y: y.value(ratios.value[bucket]),
  }
}

//: Which bucket is the day in progress, or null where the axis holds none.
const partialBucket = computed(() => {
  const at = props.buckets.findIndex((bucket) => bucket.partial)

  return at === -1 ? null : at
})

//: The buckets the line does not cross: no station reported, so there is no
//: value on this axis to plot.
const hatched = computed(() =>
    ratios.value.reduce((buckets, ratio, bucket) => {
      if (!has(ratio)) {
        buckets.push(bucket)
      }

      return buckets
    }, [])
)

/**
 * The line, as one path per unbroken run of days.
 *
 * Runs rather than one path with gaps in it, because a break has to be a
 * break: a single path that jumps a null bucket draws a straight line across
 * it, which is the interpolation the null exists to refuse.
 *
 * The day in progress is left out of the runs and drawn open below, so the
 * finished series and the day still being counted are never one stroke.
 */
const runs = computed(() => {
  const paths = []
  let run = []

  const flush = () => {
    if (run.length > 1) {
      paths.push(run.map((point, at) => `${at ? 'L' : 'M'} ${point.x} ${point.y}`).join(' '))
    }

    run = []
  }

  props.daily.forEach((_day, bucket) => {
    if (bucket === partialBucket.value || !has(ratios.value[bucket])) {
      flush()

      return
    }

    run.push(pointAt(bucket))
  })

  flush()

  return paths
})

//: A day whose neighbours both broke the line: no run to draw it on, so it
//: gets a point of its own rather than going unshown.
const isolated = computed(() =>
    props.daily.reduce((points, _day, bucket) => {
      const drawable = (at) =>
          at >= 0 && at < props.daily.length && at !== partialBucket.value && has(ratios.value[at])

      if (drawable(bucket) && !drawable(bucket - 1) && !drawable(bucket + 1)) {
        points.push(pointAt(bucket))
      }

      return points
    }, [])
)

//: Where today sits, if it has a ratio at all.
const openPoint = computed(() => {
  const bucket = partialBucket.value

  return bucket !== null && has(ratios.value[bucket]) ? pointAt(bucket) : null
})

//: The segment reaching into today, dashed, from the last finished day that
//: had a value. Nothing to draw where the day before today broke the line --
//: the point stands alone, which is itself the finding.
const openRun = computed(() => {
  const to = openPoint.value

  if (!to || to.bucket === 0 || !has(ratios.value[to.bucket - 1])) {
    return ''
  }

  const from = pointAt(to.bucket - 1)

  return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
})

/**
 * How far the day in progress has got, from the server's own clock.
 *
 * Not from the window's ``until``, which at daily grain is tomorrow midnight
 * and says nothing about how much of today has been counted.
 */
const asOfHour = computed(() => {
  const at = new Date(props.asOf)

  return `${String(at.getUTCHours()).padStart(2, '0')}:00 UTC`
})

/**
 * One day in words, including which kind of nothing a hatched day is.
 *
 * This is the only place the two null cases are told apart. The mark says
 * "no value on this axis" and is not allowed to say more, so a reader
 * pointing at a hatched stub -- or listening to it -- is told here whether
 * the centre was silent or whether its traffic named nobody.
 */
function describe(bucket) {
  const day = props.daily[bucket]
  const partial = props.buckets[bucket].partial
  const on = partial
      ? `Today so far, to ${asOfHour.value}`
      : formatDayLong(starts.value[bucket])

  if (!has(day.messages_per_active_station)) {
    // Which of the two hatched cases this is, decided on the volume itself
    // rather than on the station-less share of it: the sentence claims the
    // centre published *nothing*, and only the total can say that.
    if (day.messages > 0) {
      return (
          `${on}: no messages per station, because no station reported. ` +
          `${formatCount(day.messages)} messages arrived carrying no WIGOS ` +
          'identifier, so there is nothing to divide them between.'
      )
    }

    return (
        `${on}: no messages per station, because no station reported. This ` +
        'centre published nothing at all.'
    )
  }

  return (
      `${on}: ${formatCount(day.messages_per_active_station)} messages per active ` +
      `station, from ${formatCount(day.messages)} messages and ` +
      `${formatCount(day.stations)} stations.` +
      (partial ? ' The day is still being counted.' : '')
  )
}

// What the panel says when the reader is not on a bucket: what the line is
// counted over, and that a gap in it is a real state rather than missing data.
const readout = computed(() => {
  if (index.value !== null) {
    return describe(index.value)
  }

  if (!starts.value.length) {
    return ''
  }

  const broken = hatched.value.length
  const gaps = broken
      ? ` The line breaks on the ${broken === 1 ? 'one day' : `${broken} days`} ` +
      'no station reported: there is no per-station figure for a day like that.'
      : ''

  return (
      `${formatDayLong(starts.value[0])} to today, in whole UTC days.${gaps}`
  )
})

</script>

<style scoped>
.ratio-chart__line {
  fill: none;
  stroke: var(--stat-live);
  stroke-width: 1.4;
  stroke-linejoin: round;
}

.ratio-chart__point {
  fill: var(--stat-live);
}
</style>
