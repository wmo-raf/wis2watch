<template>
  <div class="map-container">
    <div ref="mapContainer" class="map"></div>

    <Card class="ws-status">
      <template #content>
        <div class="status-content">
          <Badge
              :severity="feedStatus.severity"
              :value="feedStatus.text"
          >
            <i :class="feedStatus.icon"></i>
          </Badge>
        </div>
      </template>
    </Card>

    <Card class="legend">
      <template #header>
        <div class="legend-title">
          <i class="pi pi-info-circle"></i>
          How each centre is watched
        </div>
      </template>
      <template #content>
        <div class="legend-items">
          <div v-for="item in legendItems" :key="item.label" class="legend-item">
            <Badge :severity="item.severity"/>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </template>
    </Card>

    <transition name="p-message">
      <Message v-if="showNotification" severity="success" :closable="false" class="notification">
        <i class="pi pi-check-circle"></i>
        {{ notificationMessage }}
      </Message>
    </transition>
  </div>
</template>

<script setup>
import {ref, onMounted, onBeforeUnmount, watch, computed} from 'vue'
import maplibregl from 'maplibre-gl'
import Card from 'primevue/card'
import Badge from 'primevue/badge'
import Message from 'primevue/message'

import {createBaseMap} from '@/basemap.js'
import {centrePopupHTML} from './popup-html.js'
import {REACHABILITY, REACHABILITY_ORDER} from '@/reachability.js'

const props = defineProps({
  centresByCountry: {
    type: Object,
    required: true
  },
  countryCoordinates: {
    type: Object,
    required: true
  },
  connectionStatus: {
    type: String,
    default: 'disconnected'
  },
  selectedCentreId: {
    type: String,
    default: null
  }
})

const emit = defineEmits(['centre-click'])

const mapContainer = ref(null)
const map = ref(null)

// Keyed on the centre ID rather than on a database id, because that is what
// arrives with a message: the feed says which centre published, and this is
// the marker that has to pulse for it.
const markers = ref({})

// A popup is a snapshot: maplibre puts it in the DOM and Vue never touches
// it again. Held on to so that it can be closed when the markers are rebuilt
// -- otherwise a broker that drops leaves a popup saying "Reachable" hanging
// over the marker that has just gone red.
const openPopup = ref(null)

const showNotification = ref(false)
const notificationMessage = ref('')

const legendItems = REACHABILITY_ORDER.map(state => ({
  label: REACHABILITY[state].label,
  severity: REACHABILITY[state].severity,
}))

// How the feed's own connection is shown. One row per state rather than
// three parallel switches over the same value, which is how a state comes to
// be handled in two of them and forgotten in the third.
const FEED_STATUS = {
  connected: {severity: 'success', icon: 'pi pi-check-circle', text: 'Feed connected'},
  disconnected: {severity: 'danger', icon: 'pi pi-times-circle', text: 'Feed disconnected'},
  error: {severity: 'danger', icon: 'pi pi-exclamation-circle', text: 'Feed error'},
}

// `connecting`, and anything a reconnect leaves it in on the way there.
const CONNECTING = {severity: 'warn', icon: 'pi pi-spinner pi-spin', text: 'Connecting...'}

const feedStatus = computed(() => FEED_STATUS[props.connectionStatus] || CONNECTING)

let teardownMap = null

onMounted(() => {
  initMap()
})

onBeforeUnmount(() => {
  teardownMap?.()
  teardownMap = null
})

/**
 * What the markers are actually drawn from: where each one goes and what
 * colour it is.
 *
 * Watched instead of the centres themselves because the status is asked for
 * again every half minute, and each answer is a fresh array however little
 * has changed in it. Watching that deeply rebuilt every marker on the clock,
 * which closed whatever popup the reader had open -- twice a minute, for
 * nothing.
 */
const markerSignature = computed(() =>
    Object.entries(props.centresByCountry)
        .map(([countryCode, centres]) => [
          countryCode,
          (props.countryCoordinates[countryCode] || []).join(','),
          centres.map(centre => `${centre.centre_id}=${centre.reachability}`).join('|'),
        ].join(':'))
        .join(';')
)

watch(markerSignature, () => {
  updateMarkers()
})

watch(() => props.selectedCentreId, (centreId) => {
  if (centreId && markers.value[centreId]) {
    flyToCentre(centreId)
  }
})

// The basemap, its controls and the theme flip all live in the helper. The
// markers below are DOM overlays rather than map layers, so they ride over a
// style change untouched -- but the helper's `transformStyle` is what keeps
// that true for anything this map ever adds as a real source or layer.
const initMap = () => {
  const {map: baseMap, ready, destroy} = createBaseMap(mapContainer.value, {
    center: [20, 10],
    zoom: 2
  })

  map.value = baseMap
  teardownMap = destroy

  ready.then(() => {
    updateMarkers()
  })
}

const updateMarkers = () => {
  openPopup.value?.remove()
  openPopup.value = null

  Object.values(markers.value).forEach(({marker}) => {
    marker.remove()
  })
  markers.value = {}

  // One marker per centre, placed at the centre of the country it publishes
  // for. Several centres in one country therefore sit on the same point --
  // which is what the sidebar listing is for.
  Object.entries(props.centresByCountry).forEach(([countryCode, centres]) => {
    const centerPoint = props.countryCoordinates[countryCode]

    if (!centerPoint) {
      console.warn(`⚠️ No center point for country: ${countryCode}`)
      return
    }

    centres.forEach((centre) => {
      createMarker(centre, centerPoint)
    })
  })
}

const createMarker = (centre, coords) => {
  const el = document.createElement('div')
  el.className = 'marker'

  // Set styles inline - MapLibre works better with inline styles
  el.style.width = '30px'
  el.style.height = '30px'
  el.style.borderRadius = '50%'
  el.style.border = '3px solid white'
  el.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.3)'
  el.style.cursor = 'pointer'
  el.style.backgroundColor = REACHABILITY[centre.reachability].colour

  const marker = new maplibregl.Marker({
    element: el,
    draggable: false
  })
      .setLngLat(coords)
      .addTo(map.value)

  el.addEventListener('click', (e) => {
    e.stopPropagation()
    showPopup(centre, coords)
    emit('centre-click', centre.centre_id)
  })

  markers.value[centre.centre_id] = {marker, element: el, coords}
}

const showPopup = (centre, coords) => {
  openPopup.value?.remove()

  openPopup.value = new maplibregl.Popup({closeButton: true, className: 'prime-popup'})
      .setLngLat(coords)
      .setHTML(centrePopupHTML(centre))
      .addTo(map.value)
}

const flyToCentre = (centreId) => {
  if (markers.value[centreId]) {
    const {coords} = markers.value[centreId]
    map.value.flyTo({center: coords, zoom: 6})
  }
}

/**
 * Pulse the marker of the centre a message was published by.
 *
 * A centre publishing under an ID no node is registered for has no marker,
 * and nothing happens. That is the honest outcome: there is nowhere on this
 * map for it to happen.
 */
const pulseCentre = (centreId) => {
  if (markers.value[centreId]) {
    const {element} = markers.value[centreId]
    element.classList.add('pulse')
    setTimeout(() => element.classList.remove('pulse'), 2000)
  }
}

const showNotif = (message) => {
  notificationMessage.value = message
  showNotification.value = true
  setTimeout(() => {
    showNotification.value = false
  }, 3000)
}

/**
 * Show a temporary pulsing data point at a specific location.
 * Used to show where an incoming notification's observation was made.
 *
 * @param {Object} geometry - GeoJSON geometry object with coordinates
 * Example: {type: "Point", coordinates: [lon, lat, elevation]}
 */
const showDataPoint = (geometry) => {
  if (!map.value) {
    return
  }

  if (!geometry || geometry.type !== 'Point' || !geometry.coordinates) {
    return
  }

  const [lon, lat] = geometry.coordinates

  const el = document.createElement('div')
  el.className = 'data-point'

  const tempMarker = new maplibregl.Marker({
    element: el,
    draggable: false,
    anchor: 'center'
  })
      .setLngLat([lon, lat])
      .addTo(map.value)

  // Remove marker after the animation has run out
  setTimeout(() => {
    tempMarker.remove()
  }, 5000)
}

defineExpose({
  pulseCentre,
  showNotif,
  flyToCentre,
  showDataPoint
})
</script>

<style scoped>
.map-container {
  flex: 1;
  position: relative;
  height: 100%;
}

.map {
  width: 100%;
  height: 100%;
}

.ws-status {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 1000;
  min-width: 150px;
}

.ws-status :deep(.p-card-body) {
  padding: 0.75rem;
}

.ws-status :deep(.p-card-content) {
  padding: 0;
}

.status-content {
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-content :deep(.p-badge) {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  padding: 0.5rem 0.75rem;
}

.legend {
  position: absolute;
  bottom: 30px;
  left: 10px;
  z-index: 1000;
  min-width: 200px;
}

.legend :deep(.p-card-body) {
  padding: 1rem;
}

.legend-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  font-size: 0.95rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--surface-border);
}

.legend-items {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.875rem;
}

.notification {
  position: absolute;
  top: 70px;
  right: 10px;
  z-index: 1000;
  min-width: 300px;
  animation: slideInRight 0.3s ease-out;
}

@keyframes slideInRight {
  from {
    transform: translateX(100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

/* Node markers */
.marker {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 3px solid white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: box-shadow 0.3s, filter 0.3s;
}

.marker:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  filter: brightness(1.1);
}

.marker.pulse {
  animation: markerPulse 2s ease-out;
}

@keyframes markerPulse {
  0%, 100% {
    opacity: 1;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  }
  50% {
    opacity: 0.6;
    box-shadow: 0 2px 20px rgba(0, 0, 0, 0.6);
  }
}


.data-point {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: rgba(34, 197, 94, 0.9); /* Bright green */
  border: 2px solid rgba(255, 255, 255, 1);
  box-shadow: 0 0 15px rgba(34, 197, 94, 0.8),
  0 0 30px rgba(34, 197, 94, 0.4);
  animation: dataPointPulse 3s ease-out forwards;
  pointer-events: none; /* Don't interfere with map interactions */
}

@keyframes dataPointPulse {
  0% {
    transform: scale(0);
    opacity: 0;
    box-shadow: 0 0 15px rgba(34, 197, 94, 0.8),
    0 0 30px rgba(34, 197, 94, 0.4);
  }
  15% {
    transform: scale(1.3);
    opacity: 1;
    box-shadow: 0 0 20px rgba(34, 197, 94, 1),
    0 0 40px rgba(34, 197, 94, 0.6);
  }
  25% {
    transform: scale(1);
    opacity: 1;
  }
  /* Hold visible with pulsing glow */
  75% {
    transform: scale(1);
    opacity: 1;
    box-shadow: 0 0 15px rgba(34, 197, 94, 0.8),
    0 0 30px rgba(34, 197, 94, 0.4);
  }
  /* Fade out and expand */
  100% {
    transform: scale(2);
    opacity: 0;
    box-shadow: 0 0 5px rgba(34, 197, 94, 0.2);
  }
}

/* Popup styles */
:deep(.prime-popup .maplibregl-popup-content) {
  padding: 0;
  border-radius: var(--border-radius);
  overflow: hidden;
  min-width: 300px;
}

:deep(.prime-popup .p-card) {
  border: none;
  box-shadow: none;
}

:deep(.popup-header) {
  padding: 1rem;
  border-bottom: 1px solid var(--surface-border);
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

:deep(.popup-header h3) {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
}

:deep(.popup-body) {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

:deep(.info-row) {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

:deep(.info-row i) {
  color: var(--primary-color);
  width: 20px;
}

:deep(.info-row label) {
  font-weight: 600;
  color: var(--text-color-secondary);
  min-width: 100px;
}

:deep(.popup-connections) {
  padding: 1rem;
  border-top: 1px solid var(--surface-border);
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

:deep(.popup-connections-title) {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: var(--text-color-secondary);
}

:deep(.connection-row-head) {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

:deep(.connection-row-meta) {
  font-size: 0.75rem;
  color: var(--text-color-secondary);
  margin-top: 0.15rem;
}

:deep(.connection-row-error) {
  font-size: 0.75rem;
  color: var(--p-red-500);
  margin-top: 0.15rem;
}
</style>