<template>
  <div class="w2w-sidebar-wrapper">
    <div class="w2w-sidebar" :class="{ collapsed: isCollapsed }">
      <div class="w2w-sidebar-content">
        <div class="w2w-sidebar-header">
          <div class="header-top">
            <h2>WIS2 Ingest Monitoring</h2>
            <Button
                :icon="isCollapsed ? 'pi pi-chevron-right' : 'pi pi-chevron-left'"
                text
                rounded
                severity="secondary"
                size="small"
                class="collapse-btn"
                @click="toggleSidebar"
            />
          </div>
          <div class="stats-grid">
            <Card v-for="stat in statsData" :key="stat.label" class="stat-card"
                  :style="{ borderTop: `4px solid ${stat.color}` }">
              <template #content>
                <div class="stat-label">{{ stat.label }}</div>
                <div class="stat-value">{{ stat.value }}</div>
              </template>
            </Card>
          </div>
        </div>

        <ConnectionList :connections="connections"/>

        <div class="filter-controls">
          <SelectButton
              v-model="selectedFilter"
              :options="filters"
              optionLabel="label"
              optionValue="value"
              :allowEmpty="false"
              @change="$emit('filter-change', selectedFilter)"
          />
        </div>

        <Divider/>

        <div class="node-list">
          <Card
              v-for="centre in centres"
              :key="centre.centre_id"
              class="node-card"
              :class="{ selected: centre.centre_id === selectedCentreId }"
              @click="$emit('centre-select', centre.centre_id)"
          >
            <template #content>
              <div class="node-header">
                <Badge
                    :severity="REACHABILITY[centre.reachability].severity"
                    :value="REACHABILITY[centre.reachability].label"
                />
                <div class="node-info">
                  <div class="node-name">
                    <i class="pi pi-server"></i>
                    {{ centre.name }}
                  </div>
                  <div class="node-country">
                    <i class="pi pi-globe"></i>
                    {{ centre.country }}
                    <span class="node-centre-id">{{ centre.centre_id }}</span>
                  </div>
                </div>
              </div>

              <div v-if="centre.connections.length" class="node-stats">
                <Chip
                    v-for="connection in centre.connections"
                    :key="connection.source_id"
                    :label="`${sourceTypeLabel(connection)} · ${formatRelativeTime(connection.last_connected_at)}`"
                    icon="pi pi-link"
                />
              </div>
              <div v-else class="node-note">
                {{ REACHABILITY[centre.reachability].note }}
              </div>
            </template>
          </Card>
        </div>
      </div>
    </div>
    <Button
        v-if="isCollapsed"
        class="toggle-sidebar-btn"
        :icon="'pi pi-chevron-right'"
        rounded
        size="large"
        @click="toggleSidebar"
        v-tooltip.right="'Show Sidebar'"
    />
  </div>
</template>

<script setup>
import {computed, ref} from 'vue'
import Card from 'primevue/card'
import SelectButton from 'primevue/selectbutton'
import Badge from 'primevue/badge'
import Chip from 'primevue/chip'
import Button from 'primevue/button'
import Divider from 'primevue/divider'

import ConnectionList from './ConnectionList.vue'
import {formatRelativeTime} from './relative-time.js'
import {REACHABILITY, REACHABLE, UNDIALLED, UNREACHABLE, sourceTypeLabel} from '@/reachability.js'

const props = defineProps({
  centres: {
    type: Array,
    required: true
  },
  connections: {
    type: Array,
    required: true
  },
  stats: {
    type: Object,
    required: true
  },
  currentFilter: {
    type: String,
    default: 'all'
  },
  selectedCentreId: {
    type: String,
    default: null
  }
})

defineEmits(['filter-change', 'centre-select'])

const isCollapsed = ref(false)
const selectedFilter = ref(props.currentFilter)

// Three of the four states a centre can be in, plus everything. The fourth,
// "not yet asked", is a state a broker passes through in the minutes after a
// catalogue sync advertises it, and a button of its own for it would sit at
// nought all day; All is where it is found.
const filters = [
  {value: 'all', label: 'All'},
  {value: REACHABLE, label: REACHABILITY[REACHABLE].label},
  {value: UNREACHABLE, label: REACHABILITY[UNREACHABLE].label},
  {value: UNDIALLED, label: REACHABILITY[UNDIALLED].label},
]

// Centres and connections are counted separately on purpose: they are not
// the same population. One Global Broker connection can be the whole reason
// a hundred centres are being seen at all.
const statsData = computed(() => [
  {label: 'Centres', value: props.stats.totalCentres, color: 'var(--p-amber-500)'},
  {label: 'Connections', value: props.stats.totalConnections, color: 'var(--p-blue-500)'},
  {label: 'Reachable', value: props.stats.reachableConnections, color: REACHABILITY[REACHABLE].colour},
  {label: 'Unreachable', value: props.stats.unreachableConnections, color: REACHABILITY[UNREACHABLE].colour},
])

const toggleSidebar = () => {
  isCollapsed.value = !isCollapsed.value
}
</script>

<style scoped>
.w2w-sidebar-wrapper {
  position: relative;
  display: flex;
}

.w2w-sidebar {
  width: 380px;
  background: var(--surface-ground);
  box-shadow: 0 0 20px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: width 0.3s ease, min-width 0.3s ease;
  min-width: 380px;
  flex-shrink: 0;
  position: relative;
  z-index: 100;
}

.w2w-sidebar.collapsed {
  width: 0;
  min-width: 0;
  box-shadow: none;
}

.w2w-sidebar-content {
  width: 380px;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.w2w-sidebar-header {
  padding: 1.5rem;
  background: var(--primary-color);
  color: var(--primary-color-text);
  flex-shrink: 0;
}

.header-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
}

.header-top h2 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}

.collapse-btn {
  color: var(--primary-color-text) !important;
}

.collapse-btn:hover {
  background: rgba(255, 255, 255, 0.1) !important;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.75rem;
}

.stat-card {
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.stat-card :deep(.p-card-body) {
  padding: 0.75rem;
}

.stat-card :deep(.p-card-content) {
  padding: 0;
  text-align: center;
}

.stat-label {
  font-size: 0.75rem;
  opacity: 0.9;
  text-transform: uppercase;
  font-weight: 500;
  color: var(--primary-color-text);
}

.stat-value {
  font-size: 1.75rem;
  font-weight: 700;
  margin-top: 0.25rem;
  color: var(--primary-color-text);
}

.filter-controls {
  padding: 1rem;
  background: var(--surface-50);
  flex-shrink: 0;
}

.filter-controls :deep(.p-selectbutton) {
  width: 100%;
}

/* PrimeVue 4 builds a SelectButton out of ToggleButtons, so `.p-button` --
   which is what this rule named until the strip grew a two-word label and
   the wrap gave it away -- reaches nothing here. */
.filter-controls :deep(.p-togglebutton) {
  font-size: 0.75rem;
  padding: 0.5rem;
}

/* Four labels across 380px, one of them two words. Without this the longest
   wraps and the strip grows a second line. */
.filter-controls :deep(.p-togglebutton-label) {
  white-space: nowrap;
}

.node-list {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  gap: 0.75rem;
  display: flex;
  flex-direction: column;
}

.node-card {
  cursor: pointer;
  transition: all 0.2s;
  border: 2px solid transparent;
  flex-shrink: 0;
}

.node-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.node-card.selected {
  border-color: var(--primary-color);
  background: var(--primary-50);
}

.node-card :deep(.p-card-body) {
  padding: 1rem;
}

.node-card :deep(.p-card-content) {
  padding: 0;
}

.node-header {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.node-info {
  flex: 1;
  min-width: 0;
}

.node-name {
  font-weight: 600;
  font-size: 0.95rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  color: var(--text-color);
  margin-bottom: 0.25rem;
}

.node-country {
  color: var(--text-color-secondary);
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.node-centre-id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  opacity: 0.8;
}

.node-note {
  font-size: 0.8rem;
  color: var(--text-color-secondary);
}

.node-stats {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.node-stats :deep(.p-chip) {
  font-size: 0.75rem;
}

/* Toggle button when sidebar is collapsed */
.toggle-sidebar-btn {
  position: absolute;
  left: 20px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 1000;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  animation: pulse-glow 2s infinite;
}

@keyframes pulse-glow {
  0%, 100% {
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  }
  50% {
    box-shadow: 0 4px 20px var(--primary-color);
  }
}
</style>