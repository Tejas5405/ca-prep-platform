/**
 * Render the built preview in jsdom and assert the page actually appears.
 *
 * WHY THIS IS PART OF THE BUILD, NOT A ONE-OFF CHECK
 *
 * `dist-preview` can be built successfully and produce a file that renders
 * nothing: a wrong entry point, a Tailwind source misconfiguration, or a bundle
 * that throws on load all leave a valid-looking HTML file behind. The inlining
 * script already guards the stylesheet size; this guards the other half by running
 * the exact bytes a browser would run through the real React render path and
 * reading the result.
 *
 * jsdom is already a devDependency for the unit tests, so this costs nothing new.
 */

import { readFileSync } from 'node:fs'
import { JSDOM } from 'jsdom'

const html = readFileSync('landing-preview.html', 'utf8')

const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)
if (!script) throw new Error('landing-preview.html has no inline module script')

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'https://preview.test/',
  pretendToBeVisual: true,
})

/*
 * The bundle expects a browser global scope. jsdom owns the DOM; this publishes
 * the globals the bundle touches, so the module can be evaluated as-is rather
 * than stubbed.
 */
const globals = [
  'window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'Text',
  'DocumentFragment', 'Event', 'CustomEvent', 'MouseEvent', 'KeyboardEvent',
  'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame',
  'MessageChannel', 'MutationObserver', 'IntersectionObserver', 'SVGElement',
  'localStorage', 'sessionStorage', 'history', 'location',
]
for (const key of globals) {
  if (dom.window[key] !== undefined) globalThis[key] = dom.window[key]
}

// A data: URL is the only way to import an in-memory module without writing a
// temporary file next to the build output.
await import(`data:text/javascript,${encodeURIComponent(script[1])}`)
await new Promise((resolve) => setTimeout(resolve, 250))

const read = () => dom.window.document.getElementById('root')?.textContent ?? ''
const text = read()

/*
 * Also exercise the preview's own page switcher and the sign-in screen.
 *
 * The Google button is the one control on the page bound by someone else's
 * branding rules, and the sign-in screen is unreachable in this build without the
 * switcher - so if the switcher breaks, the logo becomes invisible to exactly the
 * reviewer it exists for. Clicking it here is cheaper than discovering that in
 * someone's inbox.
 */
const signInTab = dom.window.document.querySelector('[data-preview-nav="signin"]')
if (!signInTab) throw new Error('preview switcher is missing the sign-in tab')
signInTab.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }))
await new Promise((resolve) => setTimeout(resolve, 250))
const signInText = read()

const checks = {
  'the page rendered at all': text.length > 3000,
  'headline present': text.includes('Your CA attempt, planned to the day.'),
  'calculator present': text.includes('Does your attempt actually fit?'),
  'a verdict is shown': /(Comfortable|On track|Tight|Falling short|Not viable as scoped)/.test(text),
  'the next session is labelled': /Next targetable session(January|May|September) \d{4}/.test(text),
  'ICAI figures are on the page':
    text.includes('20.09%') && text.includes('8.47%') && text.includes('14.07%'),
  'pass-rate history rendered': text.includes('Sep 25') && text.includes('Jan 26'),
  'no unrendered template values': !/\{\{|\bundefined\b|NaN|Infinity/.test(text),
  'styles are inlined': html.includes('<style>'),
  'the switcher reaches the sign-in screen': signInText.includes('Continue with Google'),
  'the Google button carries its mark': (() => {
    const button = [...dom.window.document.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === 'Continue with Google',
    )
    const paths = button?.querySelectorAll('svg path') ?? []
    return paths.length === 4
  })(),
  'the Google mark keeps its brand colours': (() => {
    const fills = [...dom.window.document.querySelectorAll('svg path')].map((p) =>
      p.getAttribute('fill'),
    )
    return ['#4285F4', '#34A853', '#FBBC05', '#EA4335'].every((c) => fills.includes(c))
  })(),
}

let failed = 0
for (const [name, ok] of Object.entries(checks)) {
  console.log(`  ${ok ? 'ok  ' : 'FAIL'}  ${name}`)
  if (!ok) failed += 1
}

if (failed > 0) {
  console.error(`\npreview verification failed (${failed} check(s))`)
  process.exit(1)
}
console.log(
  `preview verification passed (${text.length} characters of landing, ` +
    `${signInText.length} of sign-in) interaction with the switcher was exercised`,
)
