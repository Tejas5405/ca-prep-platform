/**
 * Render a server-highlighted snippet as text, never as HTML.
 *
 * PostgreSQL's `ts_headline` wraps the match in `<b>` (question search) or `<mark>`
 * (library search). Those are the only tags recognised here, and they are recognised
 * by splitting the string, not by parsing it: each piece becomes a text node, and the
 * matched piece is wrapped in a `<mark>` this component creates. A snippet whose own
 * text contains `<script>` is therefore characters on the page.
 *
 * There is no `dangerouslySetInnerHTML` on this path. `studyPages.test.tsx` fails if
 * one appears, which is the point of having one implementation rather than a copy in
 * every screen that shows a hit.
 */

export function Headline({ text }: { text: string }) {
  const parts = text.split(/<\/?(?:b|mark)>/gi)
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 ? (
          <mark key={index} className="rounded bg-brand-100 px-0.5">
            {part}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  )
}
