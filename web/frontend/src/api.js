// Talks to the FastAPI backend. Same-origin in production; proxied in dev.

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/**
 * Send the original PDF + redaction regions to the backend and get back the
 * redacted PDF as a Blob. Do NOT set Content-Type — the browser adds the
 * multipart boundary automatically.
 */
export async function redactPdf({ file, regions, quality, password }) {
  const form = new FormData()
  form.append('file', file, file.name)
  form.append('regions', JSON.stringify(regions))
  form.append('quality', quality)
  if (password) form.append('password', password)

  const res = await fetch('/api/redact', { method: 'POST', body: form })

  if (!res.ok) {
    let detail = `Request failed (HTTP ${res.status})`
    try {
      const body = await res.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status)
  }

  return await res.blob()
}
