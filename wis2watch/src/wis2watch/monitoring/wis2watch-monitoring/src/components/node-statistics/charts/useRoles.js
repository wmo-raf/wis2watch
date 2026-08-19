/**
 * The colour roles as strings, for the one kind of surface that cannot read
 * them as custom properties.
 *
 * Every SVG mark on this tab writes `fill: var(--stat-live)` and a theme flip
 * repaints it with no JavaScript at all. A canvas cannot: it is painted
 * imperatively, `fillStyle` takes a colour and not a variable, and what is
 * already on it does not change when the page's does. So a canvas has to
 * *resolve* the roles and be told to paint again -- which is the seam #66
 * watched under verdict 4, and found held.
 *
 * This is deliberately the only route to that, and it is a composable rather
 * than a function so that "and be told to paint again" cannot be forgotten by
 * the next caller: the value it hands back changes when the theme does, and a
 * component that draws from it inside a `watch` is in step by construction.
 *
 * **Two things move a role, and only one of them is a class.** Wagtail carries
 * an explicit choice as `w-theme-dark` or `w-theme-light` on `<html>`, and its
 * default is `w-theme-system`, under which the operating system decides and
 * the class never changes at all. Watching the class alone leaves the canvas
 * painted in yesterday's colours for every reader who never opened the
 * setting, which is most of them -- the same scar `roles.css` carries in its
 * selectors, for the same reason.
 *
 * Resolved against the host element rather than against `<html>`, because the
 * roles are declared on `.node-statistics` and inherited from there. An
 * element outside the island resolves every one of them to the empty string,
 * which paints nothing at all.
 */
import {onBeforeUnmount, onMounted, ref} from 'vue'

/**
 * The named roles, as colour strings that follow the theme.
 *
 * @param {import('vue').Ref<HTMLElement|null>} host - an element inside the
 *     island, which is what the custom properties are inherited through.
 * @param {string[]} names - the roles wanted, without their `--stat-` prefix.
 * @returns {import('vue').Ref<Object<string, string>>} role name to colour,
 *     replaced whenever the theme moves.
 */
export function useRoles(host, names) {
    const roles = ref({})

    //: The reader's own default: no class of Wagtail's changes when the
    //: operating system flips this, so nothing but this would repaint.
    const system = window.matchMedia('(prefers-color-scheme: dark)')

    let observer = null
    let queued = false

    function read() {
        if (!host.value) {
            return
        }

        const styles = getComputedStyle(host.value)

        roles.value = Object.fromEntries(
            names.map((name) => [name, styles.getPropertyValue(`--stat-${name}`).trim()])
        )
    }

    /**
     * Read the roles again on the next frame.
     *
     * A frame late rather than at once, and coalesced: a theme flip is a class
     * swap on `<html>` that the observer may report more than once, and the
     * values wanted are the ones that survive the whole swap.
     */
    function refresh() {
        if (queued) {
            return
        }

        queued = true
        requestAnimationFrame(() => {
            queued = false
            read()
        })
    }

    onMounted(() => {
        read()

        observer = new MutationObserver(refresh)
        observer.observe(document.documentElement, {
            attributes: true,
            attributeFilter: ['class'],
        })

        system.addEventListener('change', refresh)
    })

    onBeforeUnmount(() => {
        observer?.disconnect()
        system.removeEventListener('change', refresh)
    })

    return roles
}
