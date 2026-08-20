<template>
  <ul class="legend">
    <li v-for="state in states" :key="state.key" class="legend__key">
      <span class="legend__swatch" :class="`legend__swatch--${state.key}`"/>
      {{ state.label }}
    </li>
  </ul>
</template>

<script setup>
/**
 * Words beside every colour a presence cell can be.
 *
 * Its own component because two surfaces draw those cells -- the matrix
 * trailing the station rows and the drilldown's day strip -- and the states
 * are the one thing on either page that cannot be read off a number. Two
 * legends is how "Silent — nothing heard at all" on one page and "Silent:
 * nothing heard from this station" on the other come to describe one colour,
 * and how a state added to the cells reaches only the surface that remembered
 * it.
 *
 * The wording of the two middle states is `presence.js`'s, per grain, because
 * a day and an hour are not judged against the same thing -- and so is the
 * station-less sentence, which is the same sentence a hatched cell's tooltip
 * says.
 */
import {computed} from 'vue'

import {grainOf} from './presence.js'

const props = defineProps({
  /** The size of one cell: `day` or `hour`, the server's own spelling. */
  grain: {
    type: String,
    required: true
  },
  /**
   * Whether this surface can draw the hatched state at all. False on the
   * station rows, whose payload carries no such flag: a legend naming a
   * colour that is not on the page is a reader hunting for it.
   */
  stationLess: {
    type: Boolean,
    default: false
  },
})

const words = computed(() => grainOf(props.grain))

const states = computed(() => {
  const three = [
    {key: 'full', label: words.value.legend.full},
    {key: 'thin', label: words.value.legend.thin},
    {key: 'silent', label: 'Silent — nothing heard at all'},
  ]

  if (!props.stationLess) {
    return three
  }

  // The same sentence the cell's own tooltip carries, capitalised for a list
  // rather than rewritten -- two spellings of it is two claims to a reader.
  return [...three, {key: 'station-less', label: sentence(words.value.stationLess)}]
})

/** One of `presence.js`'s sentences, as a label rather than as a clause. */
function sentence(clause) {
  return clause.charAt(0).toUpperCase() + clause.slice(1)
}
</script>

<style scoped>
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1.25rem;
  margin: 0.5rem 0 0;
  padding: 0;
  list-style: none;
  font-size: 0.75rem;
  color: var(--w-color-text-meta);
}

.legend__key {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.legend__swatch {
  display: inline-block;
  width: 0.75rem;
  height: 0.6rem;
  border-radius: 1px;
}

.legend__swatch--full {
  background: var(--stat-live);
}

.legend__swatch--thin {
  background: var(--stat-thin);
}

/* Outlined, unlike the other two: at this size a near-ground fill on the page
   ground is a swatch a reader cannot see the edges of. */
.legend__swatch--silent {
  background: var(--stat-empty);
  box-shadow: inset 0 0 0 1px var(--stat-grid);
}

/* The hatch as a swatch, from the same two roles the SVG pattern is drawn
   with, so the legend follows the theme along with the cells it explains. A
   gradient rather than the pattern itself, because a `<defs>` cannot be
   referenced from a CSS background. */
.legend__swatch--station-less {
  background: repeating-linear-gradient(
      45deg,
      var(--stat-hatch-ground) 0 2px,
      var(--stat-hatch-line) 2px 3.5px
  );
  box-shadow: inset 0 0 0 1px var(--stat-grid);
}
</style>
