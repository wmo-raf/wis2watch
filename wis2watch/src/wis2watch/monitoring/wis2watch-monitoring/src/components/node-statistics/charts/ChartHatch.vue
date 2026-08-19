<template>
  <defs>
    <pattern
        :id="id"
        width="5"
        height="5"
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
    >
      <rect width="5" height="5" fill="var(--stat-hatch-ground)"/>
      <line x1="0" y1="0" x2="0" y2="5" stroke="var(--stat-hatch-line)" stroke-width="1.8"/>
    </pattern>
  </defs>
</template>

<script setup>
/**
 * The tab's one hatch, defined once so that every surface that means "no
 * value on this axis" draws the same diagonals.
 *
 * Painted from roles rather than from greys, so it follows the theme. A hatch
 * of hard-coded light greys is invisible on a dark ground, and the mark it
 * carries -- the difference between an hour that was silent and an hour whose
 * traffic named nobody -- is one a reader cannot recover from anywhere else
 * on the chart.
 *
 * The id is handed in rather than fixed here. Two charts on one page each
 * render their own defs, and a repeated id is a pattern that silently
 * resolves to whichever one happened to render first.
 */
defineProps({
  id: {
    type: String,
    required: true
  },
})
</script>
