<template>
  <svg
      class="cells"
      :width="width"
      :height="height"
      shape-rendering="crispEdges"
      role="img"
      :aria-label="label"
  >
    <rect
        v-for="cell in cells"
        :key="cell.index"
        :x="cell.x"
        y="1"
        :width="cellWidth"
        :height="height - 2"
        :class="`cells__cell cells__cell--${cell.state}`"
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
 */
import {computed} from 'vue'

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
  /** What to call the station in the label a screen reader is given. */
  name: {
    type: String,
    default: ''
  },
})

const width = computed(() => props.cellWidth * props.buckets.length)

// What each bucket is judged against, and what that makes it -- both from
// the one place that decides them, so that this row and the same row's line
// on the navigator wall above the table cannot come to disagree about which
// buckets were thin. The tooltip needs the ceiling as well as the state,
// because it says the figures out loud: "heard in 4 of 24 hours".
const against = computed(
    () => rowCeilings(props.values, props.ceilings, props.buckets.length)
)

const states = computed(
    () => presenceStates(props.values, props.ceilings, props.buckets.length)
)

const cells = computed(() =>
    props.buckets.map((bucket, index) => ({
      index,
      x: index * props.cellWidth,
      state: states.value[index],
      title: presenceTitle(
          bucket, props.values[index] || 0, against.value[index], props.grain
      ),
    }))
)

// Where the finished part of the window ends. An hourly axis never carries
// one -- the hour in progress is left out of the window rather than served
// half-counted -- so this is the daily axis's mark alone.
const openAt = computed(() => props.buckets.findIndex((bucket) => bucket.partial))

const label = computed(() => {
  const station = props.name || 'This station'
  const silent = cells.value.filter((cell) => cell.state === 'silent').length
  const period = grainOf(props.grain).period

  if (!silent) {
    return `${station}: heard in every one of ${cells.value.length} ${period}`
  }

  return (
      `${station}: silent in ${silent} of ${cells.value.length} ${period}, ` +
      `heard in the other ${cells.value.length - silent}`
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
</style>
