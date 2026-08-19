/**
 * What the four standings are called, in one place for the whole island.
 *
 * The figures block, the table's rows and its filter name the same four
 * things, and three lists of labels is how "Never heard from" in one and
 * "Declared, never heard from" in another end up on one page describing one
 * number. The *keys* are the API's own strings, so a filter, a row and a count
 * are the same vocabulary all the way to the server.
 *
 * The wording is deliberately not `StationStanding.LABELS`'s, and the reason
 * is the figures block rather than the table. Those figures cover every
 * station, declared or not -- a station nothing declares that stopped months
 * ago counts as gone quiet, not as undeclared -- so Python's "Declared, never
 * heard from" would be a *wrong* number there rather than a longer one. One
 * page cannot carry two spellings of one standing, so the shorter wording that
 * is true of both surfaces wins, and the Python labels stay for the surfaces
 * that filter by declaration.
 *
 * The order is the server's reading order -- what has stopped first, then what
 * was never declared, then what is working -- so a filter control offers the
 * standings in the same order the rows arrive in.
 */

export const STANDINGS = [
    {key: 'never_transmitted', label: 'Never heard from'},
    {key: 'gone_quiet', label: 'Gone quiet'},
    {key: 'undeclared', label: 'Transmitting, undeclared'},
    {key: 'transmitting', label: 'Transmitting'},
]

/** What one standing is called. */
export const STANDING_LABEL = Object.fromEntries(
    STANDINGS.map(({key, label}) => [key, label])
)

/** Where a standing sorts: what is broken first, the server's own RANK. */
export const STANDING_RANK = Object.fromEntries(
    STANDINGS.map(({key}, rank) => [key, rank])
)