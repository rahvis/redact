export default function Toolbar({
  fileName,
  color,
  setColor,
  quality,
  setQuality,
  zoom,
  setZoom,
  onUndo,
  onClear,
  onRedact,
  onReset,
  regionCount,
  busy,
}) {
  return (
    <div className="toolbar">
      <div className="tb-group tb-file" title={fileName}>
        <span className="tb-doc-icon">▤</span>
        <span className="tb-file-name">{fileName}</span>
      </div>

      <div className="tb-group">
        <span className="tb-label">Bar</span>
        <button
          className={`chip ${color === 'black' ? 'active' : ''}`}
          onClick={() => setColor('black')}
        >
          <span className="sw sw-black" /> Black
        </button>
        <button
          className={`chip ${color === 'white' ? 'active' : ''}`}
          onClick={() => setColor('white')}
        >
          <span className="sw sw-white" /> White
        </button>
      </div>

      <div className="tb-group">
        <span className="tb-label">Quality</span>
        <button
          className={`chip ${quality === 'high' ? 'active' : ''}`}
          onClick={() => setQuality('high')}
          title="150 DPI, larger file"
        >
          High
        </button>
        <button
          className={`chip ${quality === 'compressed' ? 'active' : ''}`}
          onClick={() => setQuality('compressed')}
          title="100 DPI, smaller file"
        >
          Compressed
        </button>
      </div>

      <div className="tb-group">
        <span className="tb-label">Zoom</span>
        <button
          className="chip chip-icon"
          onClick={() => setZoom((z) => Math.max(0.5, +(z - 0.25).toFixed(2)))}
        >
          −
        </button>
        <span className="tb-zoom">{Math.round(zoom * 100)}%</span>
        <button
          className="chip chip-icon"
          onClick={() => setZoom((z) => Math.min(3, +(z + 0.25).toFixed(2)))}
        >
          +
        </button>
      </div>

      <div className="tb-group">
        <button className="chip" onClick={onUndo} disabled={!regionCount}>
          Undo
        </button>
        <button className="chip" onClick={onClear} disabled={!regionCount}>
          Clear
        </button>
        <span className="tb-count">
          {regionCount} bar{regionCount === 1 ? '' : 's'}
        </span>
      </div>

      <div className="tb-group tb-right">
        <button className="chip" onClick={onReset}>
          New file
        </button>
        <button
          className="btn-primary"
          onClick={onRedact}
          disabled={busy || !regionCount}
        >
          {busy ? 'Working…' : 'Redact & Download'}
        </button>
      </div>
    </div>
  )
}
