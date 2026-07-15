import { useRef, useState } from 'react'

export default function Dropzone({ onFile, busy, status }) {
  const inputRef = useRef(null)
  const [over, setOver] = useState(false)

  const pick = (files) => {
    if (files && files.length) onFile(files[0])
  }

  return (
    <div className="dropzone-wrap">
      <div
        className={`dropzone ${over ? 'over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          pick(e.dataTransfer.files)
        }}
        onClick={() => inputRef.current?.click()}
      >
        <div className="dz-icon">▧</div>
        <div className="dz-title">Drop a PDF here, or click to choose</div>
        <div className="dz-sub">
          Your file is processed by your own instance and is never stored.
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(e) => pick(e.target.files)}
        />
      </div>

      {busy && <div className="status status-info">Loading document…</div>}
      {status && <div className={`status status-${status.type}`}>{status.msg}</div>}

      <ol className="how">
        <li>Upload your PDF.</li>
        <li>Drag black or white bars over anything sensitive.</li>
        <li>
          Click <b>Redact &amp; Download</b> — every page is flattened to an image, so
          the covered text is <b>permanently removed</b>, not just hidden.
        </li>
      </ol>
    </div>
  )
}
