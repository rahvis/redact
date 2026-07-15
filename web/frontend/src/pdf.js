// pdf.js setup. The worker MUST come from the same pdfjs-dist version as the
// API import, or you get "API version does not match Worker version". Importing
// it with Vite's `?url` bundles it as a same-origin asset (no CDN), which keeps
// the app working under a strict same-origin CSP.
import * as pdfjsLib from 'pdfjs-dist'
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc

export { pdfjsLib }

/**
 * Load a PDF from an ArrayBuffer. Throws a PasswordException (err.name) if the
 * document is encrypted and no/incorrect password is given.
 */
export async function loadPdf(data, password) {
  const params = { data }
  if (password) params.password = password
  return pdfjsLib.getDocument(params).promise
}

export const PASSWORD_EXCEPTION = 'PasswordException'
