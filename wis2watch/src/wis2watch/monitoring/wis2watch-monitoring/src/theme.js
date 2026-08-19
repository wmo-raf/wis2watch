/**
 * Which lighting the page is in, and when that changes.
 *
 * **Two things move the theme, and only one of them is a class.** Wagtail
 * carries an explicit choice as `w-theme-dark` or `w-theme-light` on `<html>`,
 * and its default is `w-theme-system`, under which the operating system
 * decides and the class never changes at all. Watching the class alone leaves
 * every reader who never opened the setting -- which is most of them -- in
 * yesterday's lighting. That scar is written into `core.js`'s PrimeVue
 * selectors and into `roles.css`'s, and it was written a third time into
 * `useRoles.js` before this file existed.
 *
 * Almost nothing needs this. A surface painted from custom properties follows
 * a theme flip with no JavaScript at all, and that is the whole of the
 * island's theming. This exists for the two surfaces that cannot: a canvas,
 * which is painted imperatively and does not change when the page's colours
 * do, and MapLibre, whose basemap is a *style document* fetched by URL rather
 * than a colour anything can inherit.
 */

/**
 * Is the page in its dark lighting right now?
 *
 * @returns {boolean}
 */
export function isDarkTheme() {
    const classes = document.documentElement.classList

    if (classes.contains('w-theme-dark')) {
        return true
    }

    if (classes.contains('w-theme-light')) {
        return false
    }

    //: `w-theme-system`, and anything else: the reader never chose, so the
    //: operating system is the answer.
    return window.matchMedia('(prefers-color-scheme: dark)').matches
}

/**
 * Call `onChange` whenever the theme may have moved.
 *
 * A frame late rather than at once, and coalesced: a theme flip is a class
 * swap on `<html>` that the observer may report more than once, and what the
 * caller wants is the state that survives the whole swap.
 *
 * @param {() => void} onChange
 * @returns {() => void} stop watching; callers must call this on unmount.
 */
export function watchTheme(onChange) {
    const system = window.matchMedia('(prefers-color-scheme: dark)')

    let queued = false

    function refresh() {
        if (queued) {
            return
        }

        queued = true
        requestAnimationFrame(() => {
            queued = false
            onChange()
        })
    }

    const observer = new MutationObserver(refresh)
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['class'],
    })

    system.addEventListener('change', refresh)

    return () => {
        observer.disconnect()
        system.removeEventListener('change', refresh)
    }
}
