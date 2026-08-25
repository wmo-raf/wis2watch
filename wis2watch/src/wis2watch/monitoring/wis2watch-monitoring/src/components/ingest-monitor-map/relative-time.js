/**
 * How long ago something happened, in words.
 *
 * "Last connected 2026-08-25T09:14:03+00:00" answers a question nobody
 * asked; what a reader wants from a connection's timestamp is whether it is
 * recent. The clock is a parameter so that this can be asserted about at all.
 */
export const formatRelativeTime = (timestamp, now = Date.now()) => {
    if (!timestamp) {
        return 'Never'
    }

    const then = new Date(timestamp).getTime()

    if (Number.isNaN(then)) {
        return 'Never'
    }

    const seconds = Math.floor((now - then) / 1000)
    const minutes = Math.floor(seconds / 60)
    const hours = Math.floor(minutes / 60)
    const days = Math.floor(hours / 24)

    if (seconds < 30) {
        return 'Just now'
    }

    if (seconds < 60) {
        return `${seconds}s ago`
    }

    if (minutes < 60) {
        return `${minutes}m ago`
    }

    if (hours < 24) {
        return `${hours}h ago`
    }

    if (days === 1) {
        return 'Yesterday'
    }

    if (days < 7) {
        return `${days} days ago`
    }

    return new Date(timestamp).toLocaleDateString()
}
