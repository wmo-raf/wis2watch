<template>
  <button
      type="button"
      class="button button--icon text-replace all-nodes__refresh"
      :aria-label="inFlight ? 'Refreshing' : 'Refresh'"
      :disabled="inFlight"
      @click="$emit('refresh')"
  >
    <svg
        class="icon"
        :class="inFlight ? 'icon-spinner' : 'icon-rotate'"
        aria-hidden="true"
    >
      <use :href="inFlight ? '#icon-spinner' : '#icon-rotate'"/>
    </svg>
  </button>
</template>

<script setup>
/**
 * Ask for the region again.
 *
 * A component of its own only because it is mounted in two different places
 * and must be the same button in both. On the admin home it is teleported into
 * Wagtail's panel header, where the panel's actions belong; on the overview
 * page there is no panel header to teleport into, so it renders where it
 * stands. Writing the markup twice is how the two would come to differ in
 * their label, their icon, or what disabled looks like.
 *
 * Wagtail's own `.button--icon` styles it, so this carries almost nothing of
 * its own -- and the icon is drawn from the sprite the admin page already
 * injects rather than from anything this bundle ships.
 */
defineProps({
  /** Whether a request is in the air, first read or later one. */
  inFlight: {
    type: Boolean,
    default: false
  },
})

defineEmits(['refresh'])
</script>
