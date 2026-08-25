<template>
  <svg
      class="cells"
      :width="width"
      :height="height"
      shape-rendering="crispEdges"
      role="img"
      :aria-label="label"
  >
    <!-- Only where this row can actually draw a hatched cell. A pattern per row
         on a table of a thousand of them is a thousand `defs`, so neither
         surface pays for the other's: the drilldown carries the station-less
         flag and no baseline, the station rows carry a baseline and no flag,
         and a row with a baseline it can be judged against needs neither. -->
    <ChartHatch v-if="stationLess || unjudged" :id="hatchId"/>

    <rect
        v-for="cell in cells"
        :key="cell.index"
        :x="cell.x"
        y="1"
        :width="cellWidth"
        :height="height - 2"
        :class="`cells__cell cells__cell--${cell.state}`"
        :fill="hatched(cell) ? `url(#${hatchId})` : undefined"
    >
      <title>{{ cell.title }}</title>
    </rect>

    <!-- The day still being counted, marked the way every other panel on the
         tab marks it: the charts' own dashed stroke in the live colour, along
         the edge where the finished part of the window ends. It is the open
         bucket's *left* side rather than the whole outline, and that is a
         concession to the size of the thing: at five pixels by twelve, a
         dashed box around one cell closes it -- which reads as a border, the
         opposite of unfinished -- and repeats down every row of the table.
         Drawn per row, it composes into one dashed rule the height of the
         matrix, and nothing to its right has finished. -->
    <line
        v-if="openAt !== -1"
        :x1="openAt * cellWidth"
        :x2="openAt * cellWidth"
        y1="0"
        :y2="height"
        class="stat-open"
    />
  </svg>
</template>

<script setup>
/**
 * One station's presence vector, as the trailing cells of its own row.
 *
 * This is the availability matrix, and it is *not* a chart of its own: there
 * is no axis here, no sort, no scroll and no hover layer. It draws the vector
 * it is handed against the axis it is handed, both of which belong to the
 * table around it -- which is what stops the matrix and the list of stations
 * beside it ever disagreeing about which row is which.
 *
 * Three states, and the thresholds are `presence.js`'s. What is here is the
 * drawing: a rectangle per bucket, a native `<title>` on each of them, and
 * the open-bucket mark on the day still being counted. Native tooltips rather
 * than the charts' shared hover card, because a screen of these carries
 * thousands of cells and a card is a component per cell.
 *
 * A fourth state exists where a surface hands one in, and it is not a state
 * of the *station*: a bucket the centre published in and named nobody in gets
 * the tab's hatch -- "no value on this axis" -- rather than the silent
 * colour, because painting it as silence would blame a station for its
 * centre's attribution gap. It wins over whatever the station's own vector
 * says for that bucket, which is what "uniformly hatched" means: the bucket
 * is one the matrix cannot speak about.
 */
import {computed, useId} from 'vue'

import ChartHatch from './charts/ChartHatch.vue'
import {grainOf, presenceStates, presenceTitle, rowCeilings} from './presence.js'

const props = defineProps({
  /** What the station was heard doing in each bucket, positional and dense. */
  values: {
    type: Array,
    required: true
  },
  /** The window's own axis, the same one every other row is drawn against. */
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
   * its own busiest bucket" -- which is what an hourly axis gets, there being
   * no number of messages an hour is full at.
   */
  ceilings: {
    type: Array,
    default: null
  },
  /** How wide one cell is drawn, decided once for the whole table. */
  cellWidth: {
    type: Number,
    required: true
  },
  /**
   * How tall the row is, in pixels. Required rather than defaulted, because
   * the row height belongs to the table -- it is what the virtual list counts
   * in -- and a default here would be a second copy of it, free to drift.
   */
  height: {
    type: Number,
    required: true
  },
  /**
   * How much of a whole bucket this centre normally hears this station in, or
   * null where too little of its history is known to say. It is what the day
   * grain judges a cell against -- the clock was, until #112 measured two
   * thirds of every pale cell to be a station sitting at its own normal level.
   * Null draws the row unjudged rather than guessing it.
   */
  baseline: {
    type: Number,
    default: null
  },
  /** What to call the station in the label a screen reader is given. */
  name: {
    type: String,
    default: ''
  },
  /**
   * Which buckets the centre published in and named no station at all in, or
   * null where the surface has no way to know. The station rows do not carry
   * it; the drilldown does.
   */
  stationLess: {
    type: Array,
    default: null
  },
})

// One per component instance, because two of these on a page each render
// their own `defs` and a repeated id is a pattern that silently resolves to
// whichever one happened to render first.
const hatchId = useId()

const width = computed(() => props.cellWidth * props.buckets.length)

// What each bucket is judged against, and what that makes it -- both from
// the one place that decides them, so that this row and the same row's line
// on the navigator wall above the table cannot come to disagree about which
// buckets were thin. The tooltip needs the ceiling as well as the state,
// because it says the figures out loud: "heard in 4 of 24 hours".
const against = computed(
    () => rowCeilings(props.values, props.ceilings, props.buckets.length, props.baseline)
)

const states = computed(() =>
    presenceStates(
        props.values, props.ceilings, props.buckets.length, props.baseline
    ).map((state, at) => (isStationLess(at) ? 'station-less' : state))
)

const cells = computed(() =>
    props.buckets.map((bucket, index) => ({
      index,
      x: index * props.cellWidth,
      state: states.value[index],
      title: presenceTitle(
          bucket,
          props.values[index] || 0,
          against.value?.[index],
          props.grain,
          isStationLess(index)
      ),
    }))
)

/** Whether the centre published in this bucket and named nobody in it. */
function isStationLess(at) {
  return Boolean(props.stationLess?.[at])
}

/*
 * Whether this row has no scale to be judged against at all.
 *
 * The hatch, on its second meaning and not a second mark. #51 fixed what the
 * pattern says -- *no value on this axis* -- and a station nothing has been
 * learned about has none: it was heard, and whether that is a lot for it is a
 * question with no answer yet. The station-less cell is the same claim about a
 * different axis, which is why one pattern serves both and why the legend
 * names whichever of them its surface can draw.
 */
const unjudged = computed(
    () => Boolean(props.ceilings) && props.baseline == null
)

/** Whether this cell is drawn as a pattern rather than a colour. */
function hatched(cell) {
  return cell.state === 'station-less' || cell.state === 'unjudged'
}

// Where the finished part of the window ends. An hourly axis never carries
// one -- the hour in progress is left out of the window rather than served
// half-counted -- so this is the daily axis's mark alone.
const openAt = computed(() => props.buckets.findIndex((bucket) => bucket.partial))

const label = computed(() => {
  const station = props.name || 'This station'
  const silent = cells.value.filter((cell) => cell.state === 'silent').length
  const unnamed = cells.value.filter((cell) => cell.state === 'station-less').length
  // Said rather than left to the hatch, and said before the count of silence
  // so that the two are not read as one number: a bucket the centre named
  // nobody in is not a bucket this station was silent in.
  const attribution = unnamed
      ? ` In ${unnamed} of them this centre published nothing naming any` +
      ` station, so nothing can be said about this one.`
      : ''
  const period = grainOf(props.grain).period

  if (!silent) {
    return `${station}: heard in every one of ${cells.value.length} ${period}.${attribution}`
  }

  return (
      `${station}: silent in ${silent} of ${cells.value.length} ${period}, ` +
      `heard in the other ${cells.value.length - silent}.${attribution}`
  )
})
</script>

<style scoped>
.cells {
  display: block;
}

/* Known, and nothing heard. The ground of the matrix rather than a mark on
   it: what a reader is hunting for is the *absence* of the live colour in a
   run of cells, and a silent cell painted as loudly as a live one would make
   every centre with a dead cohort a wall of colour. */
.cells__cell--silent {
  fill: var(--stat-empty);
}

/* Heard, but for a small part of the bucket. A step toward the ground in
   whichever direction the theme runs -- the role carries that, and this must
   not reach for a colour of its own. */
.cells__cell--thin {
  fill: var(--stat-thin);
}


.cells__cell--full {
  fill: var(--stat-live);
}

/* The station-less bucket has no rule at all, and that is deliberate: the pattern
   is bound as a presentation attribute on the rect, and any `fill` here --
   including `none` -- would beat it, because CSS wins over presentation
   attributes. The class stays as a hook for anything that is not a fill. */
</style>
