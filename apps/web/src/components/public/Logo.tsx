/**
 * The brand mark, in one place.
 *
 * It was inline inside `Landing.tsx` while the landing page was the only public
 * screen. The moment a second public page exists, a second copy of the mark
 * exists, and within a month the two differ by a stroke width nobody can explain.
 * Inline SVG rather than a file: the mark costs no request, scales with text, and
 * inherits the theme's brand colour.
 */

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 26 26"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect x="0.75" y="0.75" width="24.5" height="24.5" rx="6" className="fill-brand-600" />
      <path
        d="M8 17.5V8.5h3.2c2.4 0 3.9 1.7 3.9 4.5s-1.5 4.5-3.9 4.5H8Z"
        className="fill-white"
      />
      <path d="M17.2 8.5h1.4l-2.6 9h-1.4l2.6-9Z" className="fill-brand-100" />
    </svg>
  )
}

/** Mark plus wordmark. Used in the navbar and the footer. */
export function Wordmark({ className = '' }: { className?: string }) {
  return (
    <span className={`flex items-center gap-2.5 ${className}`}>
      <Logo />
      <span className="text-[15px] font-semibold tracking-tight text-slate-900">CA Prep</span>
    </span>
  )
}
