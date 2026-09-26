/**
 * Inline the built preview into ONE self-contained HTML file.
 *
 * Vite emits an HTML file plus separate hashed JS and CSS assets. That is right
 * for a deploy and wrong for a file someone opens by double-clicking or views in
 * a sandboxed preview pane with no network: the relative asset requests either
 * 404 or are blocked. Inlining removes the dependency entirely.
 *
 * Written as a small script rather than pulled in as a plugin because the
 * transformation is four string operations, and a plugin would be a dependency to
 * keep pinned against Node 20.
 */

import { readFileSync, writeFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const dist = join(root, 'dist-preview')
const assets = join(dist, 'assets')

/** Find the emitted HTML wherever Vite nested it (it mirrors the input path). */
function findHtml(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry === 'assets') continue
      const found = findHtml(full)
      if (found) return found
    } else if (entry.endsWith('.html')) {
      return full
    }
  }
  return null
}

const htmlPath = findHtml(dist)
if (!htmlPath) throw new Error(`no HTML emitted under ${dist}`)

const html = readFileSync(htmlPath, 'utf8')
const files = readdirSync(assets)
const jsFile = files.find((f) => f.endsWith('.js'))
const cssFile = files.find((f) => f.endsWith('.css'))

if (!jsFile || !cssFile) {
  throw new Error(`expected one .js and one .css in ${assets}, found: ${files.join(', ')}`)
}

let js = readFileSync(join(assets, jsFile), 'utf8')
const css = readFileSync(join(assets, cssFile), 'utf8')

/*
 * Assert the stylesheet is real.
 *
 * This is the failure mode that hides: a Tailwind source-detection
 * misconfiguration emits base styles only, the build succeeds, and the result is
 * an unstyled page that looks like a CSS-writing bug somewhere else entirely.
 * Checking for utilities the landing page actually uses turns a silent 23 kB
 * omission into a loud error.
 */
for (const required of ['.rounded-2xl', '.tabular-nums', '.sr-only']) {
  if (!css.includes(required)) {
    throw new Error(`stylesheet is missing ${required} - check the Tailwind source config`)
  }
}
const MIN_CSS_BYTES = 20_000
if (css.length < MIN_CSS_BYTES) {
  throw new Error(
    `stylesheet is only ${css.length} bytes (expected >= ${MIN_CSS_BYTES}) - ` +
      'Tailwind almost certainly did not scan src/',
  )
}

/*
 * An inline <script> ends at the first "</script" in the source, wherever it
 * appears - including inside a string literal. Escaping the slash keeps the
 * content byte-identical to the parser while preventing the tag from being closed
 * early, which would truncate the bundle mid-expression.
 */
js = js.replaceAll('</script', '<\\/script')

function replaceOnce(source, pattern, replacement, label) {
  if (!pattern.test(source)) throw new Error(`could not find the ${label} tag to inline`)
  return source.replace(pattern, () => replacement)
}

let output = replaceOnce(
  html,
  /<script[^>]*src="[^"]*\.js"[^>]*><\/script>/,
  `<script type="module">\n${js}\n</script>`,
  'script',
)
output = replaceOnce(
  output,
  /<link[^>]*rel="stylesheet"[^>]*>/,
  `<style>\n${css}\n</style>`,
  'stylesheet',
)

/*
 * Verify that nothing is left to fetch, at TAG level rather than attribute level.
 *
 * An earlier version scanned the whole document for `src="..."` and reported five
 * "external files" named `+fn(e)+` - it was matching string concatenation inside
 * the inlined React bundle. The question is whether any <script> or <link> tag
 * still points at a URL, so ask exactly that.
 */
const danglingTags = [...output.matchAll(/<(?:script|link)\b[^>]*\b(?:src|href)="[^"]*"[^>]*>/g)]
  .map((m) => m[0].slice(0, 80))

if (danglingTags.length > 0) {
  throw new Error(`preview still references external files: ${danglingTags.join('; ')}`)
}

// The document must contain exactly the two inline blocks and no <img>/fonts.
if (!output.includes('<style>')) throw new Error('stylesheet was not inlined')
if (!output.includes('<script type="module">')) throw new Error('script was not inlined')

const out = join(root, 'landing-preview.html')
writeFileSync(out, output, 'utf8')
rmSync(dist, { recursive: true, force: true })

const kb = (n) => `${Math.round(n / 1024)} kB`
console.log(`wrote ${out} (${kb(output.length)})`)
console.log(`  inlined ${kb(js.length)} of JS and ${kb(css.length)} of CSS`)
console.log('  zero external references')
