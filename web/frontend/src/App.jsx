import { useCallback, useState } from 'react'
import { loadPdf, PASSWORD_EXCEPTION } from './pdf.js'
import { redactPdf, ApiError } from './api.js'
import Dropzone from './components/Dropzone.jsx'
import Toolbar from './components/Toolbar.jsx'
import PageView from './components/PageView.jsx'

let regionSeq = 1

export default function App() {
  const [file, setFile] = useState(null)
  const [pdfDoc, setPdfDoc] = useState(null)
  const [numPages, setNumPages] = useState(0)
  const [regions, setRegions] = useState([])
  const [color, setColor] = useState('black')
  const [quality, setQuality] = useState('high')
  const [zoom, setZoom] = useState(1.25)
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState(null) // { type: 'info'|'error'|'success', msg }
  const [busy, setBusy] = useState(false)

  const openFile = useCallback(async (f) => {
    if (!f) return
    if (!f.name.toLowerCase().endsWith('.pdf')) {
      setStatus({ type: 'error', msg: 'Please choose a PDF file.' })
      return
    }
    setBusy(true)
    setStatus({ type: 'info', msg: 'Loading document…' })
    try {
      let pw = ''
      let doc = null
      // Retry the password up to a few times instead of dead-ending on a typo.
      for (let attempt = 0; attempt < 4 && !doc; attempt++) {
        try {
          doc = await loadPdf(await f.arrayBuffer(), pw)
        } catch (e) {
          if (e && e.name === PASSWORD_EXCEPTION) {
            pw =
              window.prompt(
                attempt === 0
                  ? 'This PDF is password-protected. Enter the password:'
                  : 'Incorrect password. Try again:',
              ) || ''
            if (!pw) throw new Error('A password is required to open this PDF.')
          } else {
            throw e
          }
        }
      }
      if (!doc) throw new Error('Incorrect password.')
      setFile(f)
      setPdfDoc(doc)
      setNumPages(doc.numPages)
      setRegions([])
      setPassword(pw)
      setStatus(null)
    } catch (e) {
      setStatus({ type: 'error', msg: `Could not open PDF: ${e.message || e}` })
    } finally {
      setBusy(false)
    }
  }, [])

  const addRegion = useCallback(
    (r) => setRegions((rs) => [...rs, { ...r, id: regionSeq++ }]),
    [],
  )
  const removeRegion = useCallback(
    (id) => setRegions((rs) => rs.filter((r) => r.id !== id)),
    [],
  )
  const undo = useCallback(() => setRegions((rs) => rs.slice(0, -1)), [])
  const clearAll = useCallback(() => setRegions([]), [])

  const doRedact = useCallback(async () => {
    if (!file) return
    if (regions.length === 0) {
      setStatus({ type: 'error', msg: 'Draw at least one redaction bar first.' })
      return
    }
    setBusy(true)
    setStatus({ type: 'info', msg: 'Redacting — flattening pages and burning in the bars…' })
    try {
      let pw = password
      let blob = null
      for (let attempt = 0; attempt < 4 && !blob; attempt++) {
        try {
          blob = await redactPdf({ file, regions, quality, password: pw })
        } catch (e) {
          if (e instanceof ApiError && e.status === 401) {
            pw =
              window.prompt(
                attempt === 0
                  ? 'The password is required to redact this PDF:'
                  : 'Incorrect password. Try again:',
              ) || ''
            if (!pw) throw new Error('A password is required.')
            setPassword(pw)
          } else {
            throw e
          }
        }
      }
      if (!blob) throw new Error('Incorrect password.')

      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.name.replace(/\.pdf$/i, '') + '_redacted.pdf'
      document.body.appendChild(a)
      a.click()
      a.remove()
      // Defer revoke: revoking on the same tick as click() can cancel the
      // download in Safari/Firefox.
      setTimeout(() => URL.revokeObjectURL(url), 2000)
      setStatus({
        type: 'success',
        msg: 'Done. Your redacted PDF was downloaded — covered content is permanently removed.',
      })
    } catch (e) {
      setStatus({ type: 'error', msg: `Redaction failed: ${e.message || e}` })
    } finally {
      setBusy(false)
    }
  }, [file, regions, quality, password])

  const reset = useCallback(() => {
    setFile(null)
    setPdfDoc(null)
    setNumPages(0)
    setRegions([])
    setPassword('')
    setStatus(null)
  }, [])

  if (!pdfDoc) {
    return (
      <div className="app">
        <Header />
        <Dropzone onFile={openFile} busy={busy} status={status} />
      </div>
    )
  }

  const pages = []
  for (let i = 1; i <= numPages; i++) {
    pages.push(
      <PageView
        key={i}
        pdfDoc={pdfDoc}
        pageNumber={i}
        scale={zoom}
        regions={regions}
        color={color}
        onAddRegion={addRegion}
        onRemoveRegion={removeRegion}
      />,
    )
  }

  return (
    <div className="app">
      <Header />
      <Toolbar
        fileName={file?.name}
        color={color}
        setColor={setColor}
        quality={quality}
        setQuality={setQuality}
        zoom={zoom}
        setZoom={setZoom}
        onUndo={undo}
        onClear={clearAll}
        onRedact={doRedact}
        onReset={reset}
        regionCount={regions.length}
        busy={busy}
      />
      {status && <div className={`status status-${status.type}`}>{status.msg}</div>}
      <div className="doc">{pages}</div>
      <footer className="footer">
        Files are processed in memory by your self-hosted CoverUP instance and are never
        stored. Redaction flattens each page to an image and rebuilds the PDF, so the
        covered content — and the original text layer — cannot be recovered.
      </footer>
    </div>
  )
}

function Header() {
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark">▧</span>
        <span className="brand-name">CoverUP</span>
        <span className="brand-tag">Secure PDF Redaction</span>
      </div>
    </header>
  )
}
