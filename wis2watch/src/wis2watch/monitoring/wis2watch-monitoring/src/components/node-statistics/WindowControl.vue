<template>
  <div class="window-control" role="group" :aria-label="label">
    <span class="window-control__label">{{ label }}</span>

    <button
        v-for="option in windows"
        :key="option.key"
        type="button"
        class="window-control__option"
        :class="{'window-control__option--chosen': option.key === modelValue}"
        :aria-pressed="option.key === modelValue"
        :disabled="busy"
        :title="option.label"
        @click="$emit('update:modelValue', option.key)"
    >
      {{ option.key }}
    </button>
  </div>
</template>

<script setup>
/**
 * How far back the tab is read over.
 *
 * The options are the ones the server published rather than four spelled out
 * here, so adding a window is a line of Python and no change at all on this
 * side -- and, more to the point, the page cannot offer a window the API
 * would refuse.
 *
 * The keys are shown as the reader's labels: `24h`, `7d`, `30d`, `90d` are
 * the same strings the API takes and the same strings the page's querystring
 * carries, so the control, the request and the shareable link are one
 * vocabulary. The prose label ("Last 7 days") is the button's title and the
 * window block's heading, which is where there is room for it.
 */
defineProps({
  /** The chosen window key, which is also the querystring value. */
  modelValue: {
    type: String,
    required: true
  },
  /** The windows the server offers: `[{key, label, grain}]`. */
  windows: {
    type: Array,
    required: true
  },
  /** True while a chosen window is still being read. */
  busy: {
    type: Boolean,
    default: false
  },
  label: {
    type: String,
    default: 'Window'
  },
})

defineEmits(['update:modelValue'])
</script>

<style scoped>
.window-control {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.window-control__label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--w-color-text-meta);
  margin-right: 0.15rem;
}

.window-control__option {
  font: inherit;
  font-size: 0.78rem;
  padding: 0.15rem 0.55rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 3px;
  background: transparent;
  color: var(--w-color-text-label);
  cursor: pointer;
}

.window-control__option:disabled {
  cursor: progress;
}

.window-control__option:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 1px;
}

/* Painted from the live role rather than from the page's furniture: the
   chosen window is a statement about the data below it, and the tab's charts
   are already bound to this colour. */
.window-control__option--chosen {
  background: var(--stat-live);
  border-color: var(--stat-live);
  color: #fff;
}
</style>
