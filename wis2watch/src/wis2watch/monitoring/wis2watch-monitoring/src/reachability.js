/**
 * How a vantage point is faring, and how each state is shown.
 *
 * Four words for the whole tool: a connection either answered, did not
 * answer, or has not been asked, and a centre may have no connection of its
 * own at all. The composable that works them out and the components that
 * draw them both read the vocabulary from here rather than one of them
 * owning it and the other borrowing.
 */

/** A vantage point that answered when it was last dialled. */
export const REACHABLE = 'reachable'

/** A vantage point that did not answer. */
export const UNREACHABLE = 'unreachable'

/** Dialled, but not tried yet. Not the same as a failure: a broker a
 *  catalogue sync has just advertised is in this state, not in the one
 *  above. */
export const UNASKED = 'unasked'

/** A centre nothing dials. Its traffic is seen on a Global Broker, so there
 *  is no connection of its own to be reachable or otherwise. Only a centre
 *  is ever in this state; being dialled is what makes a connection one. */
export const UNDIALLED = 'undialled'

/**
 * One connection's own state.
 */
export const connectionReachability = (connection) => {
    if (connection.is_reachable === true) {
        return REACHABLE
    }

    if (connection.is_reachable === false) {
        return UNREACHABLE
    }

    return UNASKED
}

/**
 * How a centre is being watched, from the connections that name it.
 *
 * Reports the worst rather than the best of several, so that a centre with
 * one connection up and another down is not drawn as healthy.
 */
export const worstReachability = (connections) => {
    if (!connections.length) {
        return UNDIALLED
    }

    const states = connections.map(connectionReachability)

    if (states.includes(UNREACHABLE)) {
        return UNREACHABLE
    }

    if (states.includes(REACHABLE)) {
        return REACHABLE
    }

    return UNASKED
}

/**
 * How each state is shown, in one place.
 *
 * The sidebar, the legend and the markers all say the same four things, and
 * a marker whose colour disagreed with the legend beside it would be worse
 * than either alone. The colours are literals rather than theme tokens
 * because the markers are DOM overlays styled inline, where a CSS variable
 * from the island's theme does not reach.
 */
export const REACHABILITY = {
    [REACHABLE]: {
        label: 'Reachable',
        severity: 'success',
        colour: '#22c55e',
        note: 'Its own broker answered when it was last dialled',
    },
    [UNREACHABLE]: {
        label: 'Unreachable',
        severity: 'danger',
        colour: '#ef4444',
        note: 'Its own broker did not answer',
    },
    [UNASKED]: {
        label: 'Not yet asked',
        severity: 'warn',
        colour: '#eab308',
        note: 'Dialled, but nothing has tried it yet',
    },
    [UNDIALLED]: {
        label: 'Not dialled',
        severity: 'secondary',
        colour: '#9ca3af',
        note: 'No connection of its own: seen on a Global Broker',
    },
}

/** The order the legend and the filters put the four states in. */
export const REACHABILITY_ORDER = [REACHABLE, UNREACHABLE, UNASKED, UNDIALLED]

/** What each kind of message source is called on screen. */
export const SOURCE_TYPE_LABELS = {
    global_broker: 'Global Broker',
    global_cache: 'Global Cache',
    origin_broker: 'Origin Broker',
    origin_api: 'Origin API',
}

export const sourceTypeLabel = (connection) =>
    SOURCE_TYPE_LABELS[connection.source_type] || connection.source_type
