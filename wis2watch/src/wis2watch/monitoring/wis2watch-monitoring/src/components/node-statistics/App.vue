<template>
  <div class="node-statistics">
    <Message severity="secondary" :closable="false">
      Statistics for {{ nodeName }}<template v-if="centreId"> ({{ centreId }})</template> are not drawn yet.
    </Message>
  </div>
</template>

<script setup>
/**
 * The node statistics dashboard.
 *
 * What is here so far is the frame: the island mounts inside the Wagtail
 * admin page, and the node it is scoped to arrives as props off the mount
 * point's dataset. Every view the dashboard is meant to carry -- the
 * headline counts, the hourly chart, the heatmap, the station matrix --
 * reads an API that does not exist yet, and each arrives on its own ticket.
 *
 * The scoping is not incidental: a station can transmit under more than one
 * centre's topics, so everything drawn here is one centre's own observation
 * rather than the station's latest anywhere.
 */
import Message from 'primevue/message'

defineProps({
  nodeId: {
    type: Number,
    required: true
  },
  nodeName: {
    type: String,
    required: true
  },
  centreId: {
    type: String,
    required: false,
    default: ''
  },
})
</script>
