<template>
  <div class="connections">
    <div class="connections-title">
      <i class="pi pi-link"></i>
      <span>Broker connections</span>
      <Badge :value="connections.length" severity="secondary"/>
    </div>

    <div v-if="!connections.length" class="connections-empty">
      <Tag severity="secondary" value="Nothing is being dialled"/>
    </div>

    <div v-else class="connections-rows">
      <div v-for="connection in connections" :key="connection.source_id" class="connection">
        <Badge :severity="REACHABILITY[connectionReachability(connection)].severity"/>
        <div class="connection-body">
          <div class="connection-name">{{ connection.name }}</div>
          <div class="connection-meta">
            <span>{{ sourceTypeLabel(connection) }}</span>
            <span class="connection-dot">·</span>
            <span>{{ REACHABILITY[connectionReachability(connection)].label }}</span>
            <span class="connection-dot">·</span>
            <span>{{ formatRelativeTime(connection.last_connected_at) }}</span>
          </div>
          <div v-if="connection.last_error" class="connection-error" :title="connection.last_error">
            {{ connection.last_error }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import Badge from 'primevue/badge'
import Tag from 'primevue/tag'

import {formatRelativeTime} from './relative-time.js'
import {REACHABILITY, connectionReachability, sourceTypeLabel} from '@/reachability.js'

/**
 * The broker connections the supervisor holds open.
 *
 * Listed on their own, above the centres, because a connection is not a
 * centre: one Global Broker carries the whole world's traffic and stands
 * over no country, so there is no marker it could be shown at. This is where
 * "the Global Broker is down" is legible, and it is the first thing a reader
 * of a quiet map needs to know.
 */
defineProps({
  connections: {
    type: Array,
    required: true
  }
})
</script>

<style scoped>
.connections {
  padding: 0.75rem 1rem;
  background: var(--surface-50);
  border-bottom: 1px solid var(--surface-border);
  flex-shrink: 0;
  max-height: 30vh;
  overflow-y: auto;
}

.connections-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: var(--text-color-secondary);
  margin-bottom: 0.75rem;
}

.connections-empty {
  padding-bottom: 0.25rem;
}

.connections-rows {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.connection {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
}

.connection-body {
  min-width: 0;
  flex: 1;
}

.connection-name {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-color);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.connection-meta {
  font-size: 0.75rem;
  color: var(--text-color-secondary);
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}

.connection-dot {
  opacity: 0.6;
}

.connection-error {
  font-size: 0.75rem;
  color: var(--p-red-500);
  margin-top: 0.15rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
