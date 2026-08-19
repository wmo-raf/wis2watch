/**
 * The page's address bar, which is the tab's only state store.
 *
 * Everything a reader chooses -- the window, and now the table's sort, filter
 * and search -- goes here, under keys that are the API's own vocabulary where
 * the API has one. The link copied out of the address bar reproduces the view
 * on screen, which is the whole reason none of this lives in a component's
 * private state.
 *
 * Written once rather than in each component that syncs something, because
 * two copies is how one of them starts pushing history entries: a reader
 * flipping through four sorts has not made four navigations, and a back
 * button that walks back through them is a back button that never leaves the
 * tab. `replaceState` throughout, so the address bar always shows what is on
 * screen and is copyable at any moment.
 */

/** What the page's URL says about one key, or a fallback where it is silent. */
export function readParam(key, fallback = '') {
    return new URLSearchParams(window.location.search).get(key) ?? fallback
}

/**
 * Put the reader's choices in the page's URL.
 *
 * An empty value takes its key out of the querystring rather than spelling it
 * as empty: a shareable link that reads `?window=24h` is the default view
 * said out loud, and `?q=&standing=` is noise a reader is asked to interpret.
 *
 * @param {Object} values - the querystring keys to set, or clear where empty.
 */
export function writeParams(values) {
    const url = new URL(window.location.href)

    for (const [key, value] of Object.entries(values)) {
        if (value === '' || value === null || value === undefined) {
            url.searchParams.delete(key)
        } else {
            url.searchParams.set(key, value)
        }
    }

    window.history.replaceState(null, '', url)
}
