<template>
  <button
      ref="trigger"
      type="button"
      class="info-note"
      :class="{'info-note--open': open}"
      :aria-label="label"
      :aria-expanded="open"
      @click="toggle"
  >
    <svg
        class="info-note__glyph"
        viewBox="0 0 16 16"
        aria-hidden="true"
        focusable="false"
    >
      <circle cx="8" cy="8" r="6.75" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <circle cx="8" cy="4.7" r="0.95" fill="currentColor"/>
      <path d="M8 7.1v4.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
    </svg>
  </button>

  <Popover
      ref="panel"
      :pt="{root: {class: 'info-note__panel'}}"
      @show="open = true"
      @hide="open = false"
  >
    <slot/>
  </Popover>
</template>

<script setup>
/**
 * The fine print of a panel, folded away behind the heading.
 *
 * Every panel on this tab is explained in prose, and the prose is the reason
 * the charts can be trusted -- but six paragraphs of it stood between the
 * reader and the first chart. What stays on the page is what a reader needs
 * without asking: what the panel is, what this centre's numbers are, and how
 * to work it. What moves in here is the methodology: how a figure is counted,
 * what it excludes, how to read one chart against another.
 *
 * The body is a slot rather than a string prop because the notes are not
 * plain text -- they emphasise words and they interpolate the window, the
 * staleness threshold, the counts. They stay written where the panel they
 * describe is written.
 *
 * `label` is required and must be distinct: eight of these sit on one page,
 * and an icon-only button with no name is eight identical "more information"
 * entries in a screen reader's list of what can be clicked.
 */
import {ref} from 'vue'

import Popover from 'primevue/popover'

defineProps({
  /**
   * The button's accessible name -- what this note is about, said in full,
   * because it is read without the heading beside it for context.
   */
  label: {
    type: String,
    required: true
  },
})

const panel = ref(null)
const open = ref(false)

const toggle = (event) => panel.value?.toggle(event)
</script>

<style scoped>
/* Sits on the heading's line rather than after it: the heading is the anchor
   in every panel, so a reader learns where to look once. */
.info-note {
  font: inherit;
  line-height: 1;
  display: inline-flex;
  vertical-align: baseline;
  align-items: center;
  margin-left: 0.35rem;
  padding: 0.1rem;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--w-color-text-meta);
  cursor: pointer;
}

.info-note:hover,
.info-note--open {
  color: var(--w-color-text-label);
}

.info-note:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 1px;
}

/* Sized in em rather than pixels: the same button hangs off a 0.95rem
   heading and a 0.7rem eyebrow, and a fixed glyph is oversized on one of
   them. */
.info-note__glyph {
  display: block;
  width: 1.15em;
  height: 1.15em;
}
</style>

<!-- Not scoped: the panel is teleported to <body>, so it is outside both this
     component's scope attribute and the `.node-statistics` element the tab's
     own custom properties are defined on. Only Wagtail's globals reach it. -->
<style>
.info-note__panel .p-popover-content {
  max-width: 42ch;
  padding: 0.6rem 0.75rem;
  font-size: 0.8rem;
  line-height: 1.5;
  color: var(--w-color-text-meta);
}
</style>
