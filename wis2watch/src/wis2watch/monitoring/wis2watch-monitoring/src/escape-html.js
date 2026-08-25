/**
 * Text, as text, for the places that build markup rather than binding it.
 *
 * Both maps hand MapLibre a popup as an HTML string, so every value in one
 * is markup until it is escaped: station names and ids come out of a
 * registry and out of observed traffic, a centre's name comes from a
 * catalogue, and a broker's last error is a raw exception message. None of
 * them are ours to trust.
 *
 * Escapes quotes as well as angle brackets, which the `textContent` trick
 * this replaced did not: a value interpolated into an attribute rather than
 * into text is one edit away in either caller.
 */
export const escapeHtml = (value) => String(value ?? '').replace(
    /[&<>"']/g,
    character => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    })[character]
)
