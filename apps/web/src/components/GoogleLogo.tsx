/**
 * The official Google "G" mark.
 *
 * WHY THIS EXISTS AS A COMPONENT
 *
 * Google's branding guidelines for Sign in with Google require the "G" mark on
 * the button, in its four official brand colours, unmodified. A text-only
 * "Continue with Google" button is both a branding violation and a measurable
 * conversion loss - the mark is the thing users recognise before they read.
 *
 * WHY IT IS INLINE SVG AND NOT AN IMAGE
 *
 *   1. No network request. The workspace preview renders in a sandboxed iframe
 *      with no network access, and the production build targets students on
 *      patchy mobile connections. An <img src="https://..."> would render as a
 *      broken box in the first case and a race in the second.
 *   2. It cannot 404. There is no asset to deploy, version or finger-print.
 *   3. It scales and inherits nothing, so the brand colours cannot be overridden
 *      by an inherited `color` or a stray CSS rule.
 *
 * DO NOT RECOLOUR, RESTYLE OR RECOMPOSE THIS MARK. The four hex values below are
 * Google's, not ours, and `login.test.tsx` asserts them exactly - a "close
 * enough" shade or a monochrome version is a trademark misuse, and a test is a
 * more reliable place to hold that line than a code review comment.
 *
 * The values are deliberately NOT exported as a constant. Exporting them would
 * let the test assert the component against itself, so a mistyped hex would
 * satisfy its own test; the test file carries Google's published values
 * independently instead.
 *
 * White/knockout variants of the mark exist for dark surfaces. Neither of our
 * buttons is dark, so only the coloured version is defined here rather than
 * shipping an unused prop.
 */

export default function GoogleLogo({ size = 18 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      /*
       * Decorative, not informative.
       *
       * The button already says "Continue with Google", so a screen reader
       * announcing "logo" as well would be noise. The mark carries brand
       * recognition for sighted users; the text carries the meaning for everyone.
       * This is also why the button must never become a bare icon button.
       */
      aria-hidden="true"
      className="shrink-0"
    >
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  )
}
