/**
 * A fake `XMLHttpRequest` for the signed storage PUT.
 *
 * WHY THIS EXISTS AND WHY IT IS SHARED
 *
 * `uploadToSignedUrl` is the one request in the web app that does not go through
 * `fetch`, because `fetch` cannot report upload progress and a 500-file batch with no
 * movement looks hung (see the comment on that function). Two test files exercise it -
 * the content screen's ingestion upload and the admin console's bulk upload - so the
 * stand-in lives here rather than being written twice and drifting.
 *
 * It records the URL, method, body and headers of every PUT, reports progress, and
 * answers with whatever status the caller configured. A real network call would fail
 * in jsdom (there is no server at `https://storage.test`), which is how a missing stub
 * shows up as a mystery "upload failed" rather than as a missing stub: hence the
 * explicit `stubbed` list the tests assert against.
 */

import { vi } from 'vitest'

export interface RecordedUpload {
  url: string
  method: string
  body: unknown
  contentType: string | null
}

export interface SignedUploadStub {
  /** Every PUT attempted, in order, including ones the stub answered with an error. */
  uploads: RecordedUpload[]
  /** How many times each URL was PUT, for retry assertions. */
  attempts: (url: string) => number
}

export interface StubOptions {
  /** Status to answer with. A function receives the URL so one batch can fail one file. */
  status?: number | ((url: string) => number) | undefined
  /** Simulate a transport failure (status 0 / `onerror`) rather than an HTTP error. */
  networkErrorFor?: (url: string) => boolean
  /** Fractions reported through `upload.onprogress` before `onload` fires. */
  progress?: number[]
  /** Answer immediately instead of on a macrotask, for tests that need no settling time. */
  sync?: boolean
}

export function stubSignedUploads(options: StubOptions = {}): SignedUploadStub {
  const uploads: RecordedUpload[] = []
  const steps = options.progress ?? [1]

  class FakeUpload {
    onprogress: ((event: ProgressEvent) => void) | null = null
  }

  class FakeXhr {
    static readonly DONE = 4
    upload = new FakeUpload()
    status = 0
    readyState = 0
    onload: (() => void) | null = null
    onerror: (() => void) | null = null

    private method = 'GET'
    private url = ''
    private contentType: string | null = null

    open(method: string, url: string) {
      this.method = method
      this.url = url
    }

    setRequestHeader(name: string, value: string) {
      if (name.toLowerCase() === 'content-type') this.contentType = value
    }

    send(body?: unknown) {
      uploads.push({ url: this.url, method: this.method, body, contentType: this.contentType })

      const status =
        typeof options.status === 'function' ? options.status(this.url) : (options.status ?? 200)
      const networkError = options.networkErrorFor?.(this.url) ?? false

      const settle = () => {
        if (networkError) {
          this.status = 0
          this.onerror?.()
          return
        }
        for (const fraction of steps) {
          this.upload.onprogress?.({
            lengthComputable: true,
            loaded: fraction,
            total: 1,
          } as ProgressEvent)
        }
        this.status = status
        this.readyState = FakeXhr.DONE
        this.onload?.()
      }

      if (options.sync) settle()
      else setTimeout(settle, 0)
    }
  }

  vi.stubGlobal('XMLHttpRequest', FakeXhr)

  return {
    uploads,
    attempts: (url: string) => uploads.filter((upload) => upload.url === url).length,
  }
}
