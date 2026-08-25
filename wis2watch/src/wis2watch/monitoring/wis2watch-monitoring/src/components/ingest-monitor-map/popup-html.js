import {formatRelativeTime} from './relative-time.js'
import {escapeHtml} from '@/escape-html.js'
import {REACHABILITY, connectionReachability, sourceTypeLabel} from '@/reachability.js'

/**
 * What a marker's popup says about a centre.
 *
 * Assembled as a string because maplibre takes one, which is why every value
 * in it goes through `escapeHtml`. Kept out of the component so that what it
 * says can be asserted about rather than looked at.
 */

const infoRow = (icon, label, value) => `
    <div class="info-row">
      <i class="pi ${icon}"></i>
      <label>${escapeHtml(label)}</label>
      <span>${escapeHtml(value)}</span>
    </div>
`

const connectionRow = (connection, now) => {
    const state = REACHABILITY[connectionReachability(connection)]

    return `
    <div class="connection-row">
      <div class="connection-row-head">
        <span class="p-badge p-badge-${state.severity}">${escapeHtml(state.label)}</span>
        <strong>${escapeHtml(connection.name)}</strong>
      </div>
      <div class="connection-row-meta">
        ${escapeHtml(sourceTypeLabel(connection))}
        &middot; last connected ${escapeHtml(formatRelativeTime(connection.last_connected_at, now))}
      </div>
      ${connection.last_error
        ? `<div class="connection-row-error">${escapeHtml(connection.last_error)}</div>`
        : ''}
    </div>
  `
}

/**
 * @param {Object} centre - a centre as `useMapNodes` merges it: the listing
 *   plus the connections that name it and the state they add up to.
 * @param {number} now - the clock the "last connected" times are read against.
 */
export const centrePopupHTML = (centre, now = Date.now()) => {
    const state = REACHABILITY[centre.reachability]

    const connections = centre.connections.length
        ? centre.connections.map(connection => connectionRow(connection, now)).join('')
        : `<div class="connection-row-meta">${escapeHtml(state.note)}</div>`

    return `
    <div class="p-card">
      <div class="popup-header">
        <span class="p-badge p-badge-${state.severity}">${escapeHtml(state.label)}</span>
        <h3>${escapeHtml(centre.name)}</h3>
      </div>

      <div class="popup-body">
        ${infoRow('pi-globe', 'Country:', centre.country)}
        ${infoRow('pi-id-card', 'Centre ID:', centre.centre_id || 'N/A')}
      </div>

      <div class="popup-connections">
        <div class="popup-connections-title">How it is watched</div>
        ${connections}
      </div>
    </div>
  `
}
