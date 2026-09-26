/**
 * Document metadata, set from the route.
 *
 * WHY THIS IS CODE AND NOT JUST TAGS IN index.html
 *
 * This is a single-page app: every public page is served from one HTML file, so
 * tags baked into `index.html` would give `/pricing`, `/faq` and `/privacy` the
 * same title and description - three pages competing for one search result, and
 * three pages that look identical when shared into a WhatsApp group, which is how
 * a CA student actually shares a link.
 *
 * WHAT THIS DOES NOT DO
 *
 * It does not make the app crawlable as well as pre-rendering would. A crawler that
 * executes JavaScript sees this; one that does not sees the `index.html` defaults,
 * which carry the product's real title and description rather than nothing.
 * Prerendering the public routes is the correct next step and is recorded as such
 * in the build audit - it is a build-configuration change (Vercel static
 * generation), not a reason to skip the per-route tags now.
 *
 * Every value here is a description of what the page contains. No ranking claims,
 * no "best", no keyword stuffing - the audience is CA students who will bounce off
 * marketing language, and unsupported claims about a study product are the kind of
 * thing that gets a site penalised.
 */

import { useEffect } from 'react'

import { SITE_URL } from '../../lib/site'

export interface SeoProps {
  title: string
  description: string
  /** Route path, e.g. `/pricing`. Used for the canonical URL. */
  path: string
}

function upsertMeta(selector: string, attrs: Record<string, string>) {
  let element = document.head.querySelector<HTMLMetaElement>(selector)
  if (!element) {
    element = document.createElement('meta')
    document.head.appendChild(element)
  }
  for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value)
}

function upsertLink(rel: string, href: string) {
  let element = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`)
  if (!element) {
    element = document.createElement('link')
    element.setAttribute('rel', rel)
    document.head.appendChild(element)
  }
  element.setAttribute('href', href)
}

export function Seo({ title, description, path }: SeoProps) {
  // No dependency array beyond the three values: remounting on a route change is
  // exactly when this must re-run, and the values are strings.
  useEffect(() => {
    const url = `${SITE_URL}${path === '/' ? '' : path}`

    document.title = title
    upsertMeta('meta[name="description"]', { name: 'description', content: description })
    upsertLink('canonical', url)

    // Open Graph: what a link looks like when a student pastes it into a group
    // chat. The absolute URL matters - a relative og:url is ignored by most
    // scrapers, and the card then falls back to the bare domain.
    upsertMeta('meta[property="og:title"]', { property: 'og:title', content: title })
    upsertMeta('meta[property="og:description"]', {
      property: 'og:description',
      content: description,
    })
    upsertMeta('meta[property="og:url"]', { property: 'og:url', content: url })
    upsertMeta('meta[property="og:type"]', { property: 'og:type', content: 'website' })
    upsertMeta('meta[property="og:site_name"]', { property: 'og:site_name', content: 'CA Prep' })
    upsertMeta('meta[property="og:locale"]', { property: 'og:locale', content: 'en_IN' })
    upsertMeta('meta[property="og:image"]', { property: 'og:image', content: `${SITE_URL}/og.svg` })
    upsertMeta('meta[property="og:image:alt"]', {
      property: 'og:image:alt',
      content: 'CA Prep — question bank, mock exams and a dated study plan for CA students',
    })

    upsertMeta('meta[name="twitter:card"]', { name: 'twitter:card', content: 'summary_large_image' })
    upsertMeta('meta[name="twitter:title"]', { name: 'twitter:title', content: title })
    upsertMeta('meta[name="twitter:description"]', {
      name: 'twitter:description',
      content: description,
    })
    upsertMeta('meta[name="twitter:image"]', {
      name: 'twitter:image',
      content: `${SITE_URL}/og.svg`,
    })

    // Structured data, kept to what is verifiably true: this is a website, it is
    // called CA Prep, and it is in English for an Indian audience. No aggregate
    // rating, no review count, no offer - all of which would be fabricated, and
    // all of which are the fields a search engine penalises when they are wrong.
    const jsonLd = {
      '@context': 'https://schema.org',
      '@type': 'WebSite',
      name: 'CA Prep',
      url: SITE_URL,
      inLanguage: 'en-IN',
      description,
    }
    let script = document.head.querySelector<HTMLScriptElement>('script[type="application/ld+json"]')
    if (!script) {
      script = document.createElement('script')
      script.type = 'application/ld+json'
      document.head.appendChild(script)
    }
    script.textContent = JSON.stringify(jsonLd)
  }, [title, description, path])

  return null
}
