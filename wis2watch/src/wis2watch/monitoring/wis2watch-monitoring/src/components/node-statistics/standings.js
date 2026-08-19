/**
 * What the four standings are called, in one place for the whole island.
 *
 * The figures block and the station table name the same four things, and two
 * lists of labels is how "Never heard from" in one and "Declared, never heard
 * from" in the other end up on one page describing one number. The Python
 * side has exactly this map for its own surfaces (`StationStanding.LABELS`);
 * this is the client's copy of it, and the values are the API's own strings so
 * a filter, a row and a count are the same vocabulary.
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

//: The standings that mean nothing has been heard from the station lately.
//: Named once, as the Python side names it once, because the table marks them
//: and the map will colour by them.
export const SILENT = ['never_transmitted', 'gone_quiet']
