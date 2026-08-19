<template>
  <svg
      class="spark"
      viewBox="0 0 100 20"
      preserveAspectRatio="none"
      role="img"
      :aria-label="label"
  >
    <line class="spark__baseline" x1="0" y1="19.4" x2="100" y2="19.4"/>

    <polyline
        class="spark__trace"
        :class="{'spark__trace--silent': silent}"
        :points="points"
        vector-effect="non-scaling-stroke"
    />
  </svg>
</template>

<script setup>
/**
 * One station's last 24 hours, as a shape.
 *
 * **Shape, not volume.** The trace is normalised to this row's own peak, so
 * it says rhythm and recency and says nothing at all about how this station
 * compares to the one above it. That is deliberate: station traffic is
 * heavy-tailed, and one dominant reporter normalised across the column would
 * flatten every other row into a flat line that looks exactly like death.
 * The comparable number is the sorted message column beside it.
 *
 * **A `viewBox`, not measured pixels** -- the opposite of every panelled
 * chart on this tab, for the opposite reason. There is no text, no axis and
 * nothing else that scaling can make illegible, and a thousand rows each
 * carrying a ResizeObserver is a thousand observers. The stroke is held at
 * its real width by `vector-effect`, so the trace does not thicken as the
 * column does.
 *
 * `preserveAspectRatio="none"` stretches the 24 hours to whatever width the
 * column has, so a slope here means nothing in absolute terms -- but every
 * trace in the column is drawn at one width, and comparing rows down the
 * column is the only comparison this drawing is for.
 *
 * **An all-zero row draws on the baseline**, which is the row this component
 * exists for. It is the commonest row on a centre in trouble, and a column of
 * flat silent lines under one heading is the fastest way there is to see a
 * dead cohort. A naive normalisation divides by zero and draws that row at
 * full height -- the loudest possible way to say "healthy" about a station
 * nothing has heard from.
 */
import {computed} from 'vue'

import {STANDING_LABEL} from './standings.js'

const props = defineProps({
  /** The last 24 whole UTC hours of message volume, dense and oldest first. */
  values: {
    type: Array,
    required: true
  },
  /** What to call the station in the label a screen reader is given. */
  name: {
    type: String,
    default: ''
  },
  /** The station's standing, so the label says the same thing the row does. */
  standing: {
    type: String,
    default: ''
  },
})

const peak = computed(() => Math.max(0, ...props.values))

// The world heard nothing from this station in 24 hours, whatever its
// standing says. The two can disagree honestly: a station heard at its own
// broker alone is transmitting and has still reached nobody, and this column
// is the one that shows it.
const silent = computed(() => peak.value === 0)

const points = computed(() => {
  const max = peak.value || 1
  const step = 100 / Math.max(1, props.values.length - 1)

  return props.values
      .map((value, index) => {
        const x = (index * step).toFixed(2)
        // Off the floor by 0.6 so a trace at zero sits *on* the baseline
        // rather than under it, and topped out at 2.4 so a peak is not
        // clipped by the stroke width.
        const y = (19.4 - (value / max) * 17).toFixed(2)

        return `${x},${y}`
      })
      .join(' ')
})

const label = computed(() => {
  const station = props.name || 'This station'
  const standing = STANDING_LABEL[props.standing]
  const said = silent.value
      ? 'nothing was heard from it in the last 24 hours'
      : `at most ${peak.value.toLocaleString()} messages in an hour over the last 24 hours`

  return standing ? `${station}, ${standing.toLowerCase()}: ${said}` : `${station}: ${said}`
})
</script>

<style scoped>
.spark {
  display: block;
  width: 100%;
  height: 20px;
}

.spark__baseline {
  stroke: var(--stat-grid);
  stroke-width: 0.6;
  vector-effect: non-scaling-stroke;
}

.spark__trace {
  fill: none;
  stroke: var(--stat-live);
  stroke-width: 1.2;
}

/* Nothing reached the world from this station today. The same red the rest of
   the tab calls silence, because a reader scanning the column is reading one
   colour vocabulary and not learning a second one here. */
.spark__trace--silent {
  stroke: var(--stat-silent);
}
</style>
