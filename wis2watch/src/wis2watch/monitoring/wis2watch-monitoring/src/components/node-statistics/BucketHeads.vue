<template>
  <svg
      :width="width"
      :height="height"
      class="heads"
      shape-rendering="crispEdges"
      tabindex="0"
      role="listbox"
      :aria-label="axisLabel"
      :aria-activedescendant="index === null ? undefined : headDomId(index)"
      @pointermove="onPointerMove($event)"
      @pointerleave="clear"
      @click="onClick($event)"
      @focus="onFocus"
      @blur="onBlur"
      @keydown="onKeydown"
  >
    <g
        v-for="(bucket, at) in buckets"
        :id="headDomId(at)"
        :key="bucket.start"
        role="option"
        :aria-selected="chosen === at"
        :aria-label="describe(at)"
        :transform="`translate(${at * cellWidth}, 0)`"
    >
      <!-- Every column gets a head, drawn as the faint rule the cells hang
           from. It is the target as well as the mark: a strip of gaps is a
           gesture that misses at five pixels a column. -->
      <rect
          :width="cellWidth"
          :height="height"
          class="heads__head"
          :class="{
            'heads__head--on': index === at,
            'heads__head--chosen': chosen === at,
          }"
      >
        <title>{{ describe(at) }}</title>
      </rect>
    </g>
  </svg>
</template>

<script setup>
/**
 * The matrix's column heads, which are the only way to pick a bucket by its
 * column rather than by its bar.
 *
 * The gesture is #74's, and it is the same gesture the charts carry: a
 * pointer, a focused index and a click all resolve through `bucketAtX` in the
 * shared hover layer, so the column a reader is told about and the column
 * they pick cannot come apart. What differs here is only the geometry --
 * fixed-width cells rather than a measured plot -- and the strip is handed
 * the same cell width the rows are drawn at, so a head cannot come to sit
 * over the wrong column.
 *
 * Why the heads exist at all: the matrix's correlation argument holds only
 * while a failure is still happening. A subset still dark forms a visible
 * band, but a subset that has already recovered is invisible under any sort,
 * because both the standing order and last-heard describe where a station
 * stands *now*. Picking the day it happened is the only route to that case,
 * and the column is where a reader who has just read a band down the matrix
 * is already looking.
 */
import {computed, useId} from 'vue'

import {useBucketHover} from './charts/useBucketHover.js'
import {grainOf} from './presence.js'
import {SELECT_HINT, bucketIndexOf} from './selection.js'

const props = defineProps({
  /** The window's own axis, the same one every row is drawn against. */
  buckets: {
    type: Array,
    required: true
  },
  /** The size of one bucket: `day` or `hour`, the server's own spelling. */
  grain: {
    type: String,
    required: true
  },
  /** How wide one column is drawn, decided once for the whole table. */
  cellWidth: {
    type: Number,
    required: true
  },
  /** The bucket picked, as the server spelled its start, or empty for none. */
  selected: {
    type: String,
    default: ''
  },
  /** What the window is called, for the label a screen reader is given. */
  windowLabel: {
    type: String,
    default: 'the window'
  },
  /**
   * How tall the strip is. Short on purpose: it is a head, and anything with
   * the height of a row reads as a first station whose name is missing.
   */
  height: {
    type: Number,
    default: 9
  },
})

const emit = defineEmits(['select'])

const width = computed(() => props.cellWidth * props.buckets.length)

//: Where the selection sits on this axis, or -1 where it names no column of
//: it -- which is what a link from another window resolves to.
const chosen = computed(() => bucketIndexOf(props.buckets, props.selected))

/** Say which bucket the reader picked, or that they dropped the selection. */
function pick(at) {
  // Picking the bucket that is already picked drops it, so the gesture that
  // made the filter is also the one that undoes it -- the pointer's way out,
  // beside Escape's.
  const start = at === null ? '' : props.buckets[at].start

  emit('select', start === props.selected ? '' : start)
}

// The same composable the charts use, and for the same reason: the arrow
// keys, Enter and Escape have to mean here exactly what they mean there, or
// the matrix is a second gesture to learn on one page.
const {index, onPointerMove, onClick, clear, onFocus, onBlur, onKeydown} =
    useBucketHover(
        () => props.buckets.length,
        () => width.value,
        {onSelect: pick}
    )

const axisLabel = computed(
    () => `The ${grainOf(props.grain).period} of ${props.windowLabel.toLowerCase()},` +
        ` oldest first. ${SELECT_HINT}`
)

//: This strip's own prefix for the ids the active-descendant points at, so
//: two tables on one page cannot both call their third column by one id.
const strip = useId()

function headDomId(at) {
  return `${strip}-head-${at}`
}

/**
 * One column in words.
 *
 * The bucket named, and what picking it would do. Not the presence sentence a
 * cell carries: a column head belongs to no station, and there is no number
 * here that is true of the whole of it.
 */
function describe(at) {
  const bucket = props.buckets[at]
  const named = grainOf(props.grain).long(new Date(bucket.start))
  const counting = bucket.partial ? ', still being counted' : ''

  return `${named}${counting} — the stations dark in it`
}
</script>

<style scoped>
.heads {
  display: block;
  cursor: pointer;
}

.heads:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 1px;
}

/* The rule the columns hang from, and the target. Faint: it is furniture over
   a matrix whose own marks are the finding. */
.heads__head {
  fill: var(--stat-grid);
  fill-opacity: 0.35;
}

/* Where the reader is, in the wash every chart on the tab uses. */
.heads__head--on {
  fill: var(--stat-hover);
  fill-opacity: 0.35;
}

/* What the table below is filtered by, in the same focus colour the charts
   outline their picked bucket with. */
.heads__head--chosen {
  fill: var(--stat-focus);
  fill-opacity: 1;
}
</style>
