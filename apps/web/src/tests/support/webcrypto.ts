/**
 * A `crypto.subtle` that works under jsdom.
 *
 * WHY THIS IS NEEDED, AND WHY IT IS NOT A PRODUCT BUG
 *
 * jsdom runs the page in its own JavaScript realm, so `File#arrayBuffer()` hands back an
 * ArrayBuffer from that realm. Node's `crypto.subtle.digest` (which is what `crypto` is
 * under jsdom) refuses it - "2nd argument is not instance of ArrayBuffer, Buffer,
 * TypedArray, or DataView" - because the `instanceof` check fails across realms. In a
 * real browser both are the page's own realm and the call succeeds, which is why the
 * bulk-upload screen hashes files in the browser at all.
 *
 * The fix is the copy: `new Uint8Array(otherRealmBuffer)` moves the bytes element by
 * element and produces a typed array in THIS realm, which the platform digest accepts.
 *
 * This only substitutes `digest`. Everything else (`randomUUID`, `getRandomValues`) is
 * passed through, so a test that reaches for them behaves as it would in a browser.
 *
 * WITHOUT IT THE UPLOAD TESTS LIED: every file failed to fingerprint, the screen marked
 * all of them "Could not read the file", and the assertions that followed still passed
 * because they did not depend on the digests being real.
 */

import { vi } from 'vitest'

export function installBrowserCrypto(): void {
  const real = globalThis.crypto

  vi.stubGlobal('crypto', {
    randomUUID: real.randomUUID.bind(real),
    getRandomValues: real.getRandomValues.bind(real),
    subtle: {
      digest: (algorithm: string, data: ArrayBuffer | ArrayBufferView) => {
        const bytes =
          data instanceof ArrayBuffer
            ? new Uint8Array(data)
            : new Uint8Array(data.buffer as ArrayBuffer, data.byteOffset, data.byteLength)
        return real.subtle.digest(algorithm, bytes)
      },
    },
  })
}
