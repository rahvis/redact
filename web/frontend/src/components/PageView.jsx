import { useEffect, useRef, useState } from 'react'

// Ignore accidental micro-drags (a click, not a bar). CSS px.
const MIN_DRAG_PX = 5

/**
 * Renders one PDF page to a crisp canvas and overlays a transparent layer on
 * which the user drags redaction bars.
 *
 * Coordinate rules (critical):
 *   - The canvas bitmap is sized viewport * devicePixelRatio for sharpness, but
 *     its CSS size stays exactly viewport.width/height. devicePixelRatio must
 *     NEVER enter the pointer/region math — that all happens in CSS pixels.
 *   - Regions are stored NORMALIZED (0..1 of the page box, top-left origin),
 *     so they are independent of zoom and of the backend's render DPI, and map
 *     1:1 onto the server's rasterized page.
 */
export default function PageView({
  pdfDoc,
  pageNumber,
  scale,
  regions,
  color,
  onAddRegion,
  onRemoveRegion,
}) {
  const canvasRef = useRef(null)
  const overlayRef = useRef(null)
  const renderTaskRef = useRef(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [drag, setDrag] = useState(null) // { x0, y0, x1, y1 } in CSS px
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function render() {
      try {
        const page = await pdfDoc.getPage(pageNumber)
        if (cancelled) return

        const viewport = page.getViewport({ scale })
        const canvas = canvasRef.current
        if (!canvas) return
        const ctx = canvas.getContext('2d')

        // Cap at 2x: beyond that the sharpness gain is invisible but the canvas
        // backing store (and memory) keeps growing — matters for big documents.
        const outputScale = Math.min(window.devicePixelRatio || 1, 2)
        const cssW = Math.floor(viewport.width)
        const cssH = Math.floor(viewport.height)

        canvas.width = Math.floor(viewport.width * outputScale)
        canvas.height = Math.floor(viewport.height * outputScale)
        canvas.style.width = `${cssW}px`
        canvas.style.height = `${cssH}px`
        setSize({ w: cssW, h: cssH })

        const transform =
          outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined

        // Cancel any in-flight render on this canvas (rapid zoom / StrictMode).
        if (renderTaskRef.current) {
          try {
            renderTaskRef.current.cancel()
          } catch {
            /* ignore */
          }
        }
        const task = page.render({ canvasContext: ctx, viewport, transform })
        renderTaskRef.current = task
        await task.promise
      } catch (e) {
        // RenderingCancelledException is expected on re-render; ignore it.
        if (!cancelled && e && e.name !== 'RenderingCancelledException') {
          setError(e.message || String(e))
        }
      }
    }

    render()
    return () => {
      cancelled = true
      if (renderTaskRef.current) {
        try {
          renderTaskRef.current.cancel()
        } catch {
          /* ignore */
        }
      }
    }
  }, [pdfDoc, pageNumber, scale])

  const pageRegions = regions.filter((r) => r.page === pageNumber - 1)

  const localXY = (e) => {
    const rect = overlayRef.current.getBoundingClientRect()
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      rect,
    }
  }

  const onMouseDown = (e) => {
    if (e.button !== 0) return
    const { x, y } = localXY(e)
    setDrag({ x0: x, y0: y, x1: x, y1: y })
  }

  const onMouseMove = (e) => {
    if (!drag) return
    const { x, y } = localXY(e)
    setDrag((d) => ({ ...d, x1: x, y1: y }))
  }

  const finishDrag = (e) => {
    if (!drag) return
    const rect = overlayRef.current.getBoundingClientRect()
    const left = Math.min(drag.x0, drag.x1)
    const top = Math.min(drag.y0, drag.y1)
    const w = Math.abs(drag.x1 - drag.x0)
    const h = Math.abs(drag.y1 - drag.y0)
    setDrag(null)
    if (w < MIN_DRAG_PX || h < MIN_DRAG_PX) return
    onAddRegion({
      page: pageNumber - 1,
      x: clamp01(left / rect.width),
      y: clamp01(top / rect.height),
      w: clamp01(w / rect.width),
      h: clamp01(h / rect.height),
      color,
    })
  }

  return (
    <div className="page">
      <div className="page-label">Page {pageNumber}</div>
      <div className="page-stage" style={{ width: size.w || undefined, height: size.h || undefined }}>
        <canvas ref={canvasRef} className="page-canvas" />
        <div
          ref={overlayRef}
          className="overlay"
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={finishDrag}
          onMouseLeave={finishDrag}
        >
          {pageRegions.map((r) => (
            <div
              key={r.id}
              className="region"
              style={{
                left: `${r.x * 100}%`,
                top: `${r.y * 100}%`,
                width: `${r.w * 100}%`,
                height: `${r.h * 100}%`,
                background: r.color === 'white' ? '#ffffff' : '#000000',
                borderColor: r.color === 'white' ? '#888' : '#222',
              }}
            >
              <button
                className="region-del"
                title="Remove this bar"
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation()
                  onRemoveRegion(r.id)
                }}
              >
                ×
              </button>
            </div>
          ))}

          {drag && (
            <div
              className="region region-temp"
              style={{
                left: Math.min(drag.x0, drag.x1),
                top: Math.min(drag.y0, drag.y1),
                width: Math.abs(drag.x1 - drag.x0),
                height: Math.abs(drag.y1 - drag.y0),
                background:
                  color === 'white' ? 'rgba(255,255,255,0.75)' : 'rgba(0,0,0,0.75)',
              }}
            />
          )}
        </div>
      </div>
      {error && <div className="page-error">Failed to render page {pageNumber}: {error}</div>}
    </div>
  )
}

function clamp01(v) {
  return Math.max(0, Math.min(1, v))
}
