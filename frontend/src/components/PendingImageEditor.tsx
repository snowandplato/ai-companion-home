import { useEffect, useRef, useState } from 'react'
import { Check, Crop, Eye, Pencil, RotateCcw, Save, Undo2, X } from 'lucide-react'
import type { ImageUpload } from '../services/api'

type EditorMode = 'view' | 'crop' | 'draw'

interface PendingImageEditorProps {
  image: ImageUpload
  imageNumber: number
  theme: 'light' | 'dark'
  onApply: (image: ImageUpload) => void
  onClose: () => void
}

interface Point {
  x: number
  y: number
}

interface CropSelection extends Point {
  width: number
  height: number
}

type CropHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'move'

interface CropDrag {
  handle: CropHandle
  start: Point
  selection: CropSelection
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value))

const brushColors = ['#E98E6B', '#716E85', '#17151C', '#FFFFFF', '#F2C94C']
const brushSizes = [3, 7, 14]

export function PendingImageEditor({ image, imageNumber, theme, onApply, onClose }: PendingImageEditorProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const overlayRef = useRef<HTMLCanvasElement | null>(null)
  const cropSelectionRef = useRef<CropSelection | null>(null)
  const cropDragRef = useRef<CropDrag | null>(null)
  const drawingRef = useRef(false)
  const originalPreviewRef = useRef(image.preview)
  const [mode, setMode] = useState<EditorMode>('view')
  const [canvasSize, setCanvasSize] = useState({ width: 1, height: 1 })
  const [brushColor, setBrushColor] = useState(brushColors[0])
  const [brushSize, setBrushSize] = useState(brushSizes[1])
  const [history, setHistory] = useState<string[]>([])
  const [canCrop, setCanCrop] = useState(false)
  const isDark = theme === 'dark'

  const renderDataUrl = (dataUrl: string) => new Promise<void>((resolve, reject) => {
    const source = new Image()
    source.onload = () => {
      const canvas = canvasRef.current
      const overlay = overlayRef.current
      if (!canvas || !overlay) return resolve()
      canvas.width = source.naturalWidth
      canvas.height = source.naturalHeight
      overlay.width = source.naturalWidth
      overlay.height = source.naturalHeight
      canvas.getContext('2d')?.drawImage(source, 0, 0)
      overlay.getContext('2d')?.clearRect(0, 0, overlay.width, overlay.height)
      setCanvasSize({ width: source.naturalWidth, height: source.naturalHeight })
      resolve()
    }
    source.onerror = () => reject(new Error('图片无法打开'))
    source.src = dataUrl
  })

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    void renderDataUrl(image.preview).then(() => setHistory([image.preview]))
    return () => {
      document.body.style.overflow = previousOverflow
    }
    // 编辑器每次会随所选图片重新挂载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const canvasPoint = (event: React.PointerEvent<HTMLCanvasElement>): Point => {
    const canvas = overlayRef.current
    if (!canvas) return { x: 0, y: 0 }
    const rect = canvas.getBoundingClientRect()
    return {
      x: (event.clientX - rect.left) * canvas.width / rect.width,
      y: (event.clientY - rect.top) * canvas.height / rect.height
    }
  }

  const clearOverlay = () => {
    const overlay = overlayRef.current
    if (overlay) overlay.getContext('2d')?.clearRect(0, 0, overlay.width, overlay.height)
  }

  const drawCropOverlay = (selection: CropSelection) => {
    const overlay = overlayRef.current
    if (!overlay) return
    const context = overlay.getContext('2d')
    if (!context) return
    context.clearRect(0, 0, overlay.width, overlay.height)
    context.fillStyle = 'rgba(18, 16, 24, 0.48)'
    context.fillRect(0, 0, overlay.width, overlay.height)
    context.clearRect(selection.x, selection.y, selection.width, selection.height)
    const displayWidth = overlay.getBoundingClientRect().width || overlay.width
    const scale = overlay.width / displayWidth
    context.strokeStyle = '#FFFFFF'
    context.lineWidth = 2 * scale
    context.strokeRect(selection.x, selection.y, selection.width, selection.height)
    const left = selection.x
    const right = selection.x + selection.width
    const top = selection.y
    const bottom = selection.y + selection.height
    const handles = [
      [left, top], [(left + right) / 2, top], [right, top],
      [right, (top + bottom) / 2], [right, bottom],
      [(left + right) / 2, bottom], [left, bottom], [left, (top + bottom) / 2]
    ]
    const size = 12 * scale
    context.fillStyle = '#FFFFFF'
    context.strokeStyle = 'rgba(25, 22, 32, 0.7)'
    context.lineWidth = 1.5 * scale
    for (const [x, y] of handles) {
      context.fillRect(x - size / 2, y - size / 2, size, size)
      context.strokeRect(x - size / 2, y - size / 2, size, size)
    }
  }

  const getCropHandle = (point: Point, selection: CropSelection, pointerType: string): CropHandle | null => {
    const overlay = overlayRef.current
    if (!overlay) return null
    const scale = overlay.width / (overlay.getBoundingClientRect().width || overlay.width)
    const hitRadius = (pointerType === 'touch' ? 24 : 14) * scale
    const left = selection.x
    const right = selection.x + selection.width
    const top = selection.y
    const bottom = selection.y + selection.height
    const handles: Array<[CropHandle, number, number]> = [
      ['nw', left, top], ['n', (left + right) / 2, top], ['ne', right, top],
      ['e', right, (top + bottom) / 2], ['se', right, bottom],
      ['s', (left + right) / 2, bottom], ['sw', left, bottom], ['w', left, (top + bottom) / 2]
    ]
    let closest: CropHandle | null = null
    let closestDistance = hitRadius
    for (const [handle, x, y] of handles) {
      const distance = Math.hypot(point.x - x, point.y - y)
      if (distance < closestDistance) {
        closest = handle
        closestDistance = distance
      }
    }
    if (closest) return closest
    if (point.x >= left && point.x <= right && point.y >= top && point.y <= bottom) return 'move'
    return null
  }

  const switchMode = (nextMode: EditorMode) => {
    setMode(nextMode)
    cropDragRef.current = null
    cropSelectionRef.current = null
    drawingRef.current = false
    setCanCrop(false)
    clearOverlay()
    if (nextMode === 'crop') {
      const canvas = canvasRef.current
      if (!canvas) return
      const insetX = canvas.width * 0.06
      const insetY = canvas.height * 0.06
      const selection = {
        x: insetX, y: insetY,
        width: canvas.width - insetX * 2,
        height: canvas.height - insetY * 2
      }
      cropSelectionRef.current = selection
      setCanCrop(true)
      drawCropOverlay(selection)
    }
  }

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (mode === 'view') return
    const point = canvasPoint(event)

    if (mode === 'crop') {
      const selection = cropSelectionRef.current
      if (!selection) return
      const handle = getCropHandle(point, selection, event.pointerType)
      if (!handle) return
      event.currentTarget.setPointerCapture(event.pointerId)
      cropDragRef.current = { handle, start: point, selection: { ...selection } }
      return
    }

    event.currentTarget.setPointerCapture(event.pointerId)

    const canvas = canvasRef.current
    const overlay = overlayRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !overlay || !context) return
    const displayRect = overlay.getBoundingClientRect()
    const displayScale = canvas.width / displayRect.width
    drawingRef.current = true
    context.beginPath()
    context.moveTo(point.x, point.y)
    context.strokeStyle = brushColor
    context.lineWidth = brushSize * displayScale
    context.lineCap = 'round'
    context.lineJoin = 'round'
  }

  const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (mode === 'crop' && cropDragRef.current) {
      const point = canvasPoint(event)
      const canvas = canvasRef.current
      if (!canvas) return
      const { handle, start, selection: initial } = cropDragRef.current
      const dx = point.x - start.x
      const dy = point.y - start.y
      let left = initial.x
      let right = initial.x + initial.width
      let top = initial.y
      let bottom = initial.y + initial.height
      const displayRect = event.currentTarget.getBoundingClientRect()
      const minWidth = Math.min(initial.width, Math.max(2, 32 * canvas.width / displayRect.width))
      const minHeight = Math.min(initial.height, Math.max(2, 32 * canvas.height / displayRect.height))
      if (handle === 'move') {
        const moveX = clamp(dx, -left, canvas.width - right)
        const moveY = clamp(dy, -top, canvas.height - bottom)
        left += moveX
        right += moveX
        top += moveY
        bottom += moveY
      } else {
        if (handle.includes('w')) left = clamp(left + dx, 0, right - minWidth)
        if (handle.includes('e')) right = clamp(right + dx, left + minWidth, canvas.width)
        if (handle.includes('n')) top = clamp(top + dy, 0, bottom - minHeight)
        if (handle.includes('s')) bottom = clamp(bottom + dy, top + minHeight, canvas.height)
      }
      const selection = { x: left, y: top, width: right - left, height: bottom - top }
      cropSelectionRef.current = selection
      drawCropOverlay(selection)
      return
    }

    if (mode === 'draw' && drawingRef.current) {
      const point = canvasPoint(event)
      const context = canvasRef.current?.getContext('2d')
      if (!context) return
      context.lineTo(point.x, point.y)
      context.stroke()
    }
  }

  const handlePointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }

    if (mode === 'crop') {
      cropDragRef.current = null
      return
    }

    if (mode === 'draw' && drawingRef.current) {
      drawingRef.current = false
      const canvas = canvasRef.current
      if (!canvas) return
      const snapshot = canvas.toDataURL(image.mimeType, image.mimeType === 'image/jpeg' ? 0.9 : undefined)
      setHistory((current) => [...current, snapshot])
    }
  }

  const applyCrop = async () => {
    const canvas = canvasRef.current
    const selection = cropSelectionRef.current
    if (!canvas || !selection || !canCrop) return
    const x = Math.max(0, Math.round(selection.x))
    const y = Math.max(0, Math.round(selection.y))
    const width = Math.min(canvas.width - x, Math.round(selection.width))
    const height = Math.min(canvas.height - y, Math.round(selection.height))
    if (width < 2 || height < 2) return

    const cropped = document.createElement('canvas')
    cropped.width = width
    cropped.height = height
    cropped.getContext('2d')?.drawImage(canvas, x, y, width, height, 0, 0, width, height)
    const snapshot = cropped.toDataURL(image.mimeType, image.mimeType === 'image/jpeg' ? 0.9 : undefined)
    await renderDataUrl(snapshot)
    setHistory((current) => [...current, snapshot])
    switchMode('view')
  }

  const undo = () => {
    if (history.length <= 1) return
    const next = history.slice(0, -1)
    setHistory(next)
    void renderDataUrl(next[next.length - 1])
    switchMode('view')
  }

  const reset = () => {
    const original = originalPreviewRef.current
    setHistory([original])
    void renderDataUrl(original)
    switchMode('view')
  }

  const finish = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const preview = canvas.toDataURL(image.mimeType, image.mimeType === 'image/jpeg' ? 0.9 : undefined)
    onApply({ base64: preview.split(',')[1], mimeType: image.mimeType, preview })
  }

  const aspectRatio = canvasSize.width / canvasSize.height
  const editorWidth = `min(${canvasSize.width}px, calc(100vw - 28px), calc((100dvh - 205px) * ${aspectRatio}))`
  const panelBackground = isDark ? '#211F27' : '#FBFAFC'
  const surface = isDark ? '#2E2C34' : '#F1EFF4'
  const text = isDark ? '#F0EDF2' : '#282531'
  const muted = isDark ? '#AAA6B2' : '#827E8C'
  const border = isDark ? '#45414E' : '#DDD9E3'

  return (
    <div role="dialog" aria-modal="true" aria-label={`编辑第${imageNumber}张图片`} style={{
      position: 'fixed', inset: 0, zIndex: 300, display: 'flex', flexDirection: 'column',
      background: isDark ? 'rgba(12, 11, 15, 0.96)' : 'rgba(31, 28, 38, 0.88)',
      backdropFilter: 'blur(14px)'
    }}>
      <div style={{
        height: '58px', padding: '0 14px', display: 'flex', alignItems: 'center', gap: '10px',
        color: '#fff', flexShrink: 0
      }}>
        <button type="button" onClick={onClose} aria-label="关闭图片预览" style={{
          width: '38px', height: '38px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.16)',
          background: 'rgba(255,255,255,0.08)', color: '#fff', display: 'grid', placeItems: 'center', cursor: 'pointer'
        }}><X size={19} /></button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '14px', fontWeight: 600 }}>第 {imageNumber} 张图片</div>
          <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.62)', marginTop: '1px' }}>
            {mode === 'view' ? '放大预览' : mode === 'crop' ? '拖动边缘、顶点或框内调整' : '直接在图片上划线'}
          </div>
        </div>
        <button type="button" onClick={finish} aria-label="完成图片编辑" title="完成图片编辑" style={{
          width: '38px', height: '38px', borderRadius: '12px', border: 'none', background: '#E98E6B',
          color: '#fff', display: 'grid', placeItems: 'center', cursor: 'pointer'
        }}><Save size={19} /></button>
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'grid', placeItems: 'center', padding: '4px 14px 12px' }}>
        <div style={{
          position: 'relative', width: editorWidth, aspectRatio: `${canvasSize.width} / ${canvasSize.height}`,
          maxWidth: '100%', maxHeight: '100%', borderRadius: '12px', overflow: 'hidden',
          boxShadow: '0 24px 80px rgba(0,0,0,0.38)', background: '#111'
        }}>
          <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
          <canvas ref={overlayRef}
            onPointerDown={handlePointerDown} onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp} onPointerCancel={handlePointerUp}
            style={{
              position: 'absolute', inset: 0, width: '100%', height: '100%',
              cursor: mode === 'view' ? 'default' : 'crosshair', touchAction: 'none'
            }} />
        </div>
      </div>

      <div style={{
        flexShrink: 0, margin: '0 10px 10px', padding: '10px', borderRadius: '16px',
        background: panelBackground, border: `1px solid ${border}`, boxShadow: '0 12px 36px rgba(0,0,0,0.22)'
      }}>
        {mode === 'draw' && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '14px', padding: '2px 4px 10px' }}>
            <div style={{ display: 'flex', gap: '7px' }}>
              {brushColors.map((color) => (
                <button key={color} type="button" aria-label={`画笔颜色 ${color}`} onClick={() => setBrushColor(color)} style={{
                  width: '27px', height: '27px', padding: 0, borderRadius: '50%', background: color,
                  border: color === '#FFFFFF' ? `1px solid ${border}` : 'none', cursor: 'pointer',
                  boxShadow: brushColor === color ? '0 0 0 3px #E98E6B55' : 'none',
                  outline: brushColor === color ? '2px solid #E98E6B' : 'none', outlineOffset: '2px'
                }} />
              ))}
            </div>
            <div style={{ width: '1px', height: '24px', background: border }} />
            <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
              {brushSizes.map((size) => (
                <button key={size} type="button" aria-label={`画笔粗细 ${size}`} onClick={() => setBrushSize(size)} style={{
                  width: '31px', height: '31px', padding: 0, borderRadius: '9px', border: 'none',
                  background: brushSize === size ? '#E98E6B22' : surface, cursor: 'pointer', display: 'grid', placeItems: 'center'
                }}>
                  <span style={{ width: `${Math.min(18, size + 3)}px`, height: `${Math.max(3, size / 2)}px`, borderRadius: '99px', background: text }} />
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <div style={{ display: 'flex', gap: '6px' }}>
            {([
              { value: 'view' as const, label: '查看', icon: Eye },
              { value: 'crop' as const, label: '裁切', icon: Crop },
              { value: 'draw' as const, label: '画笔', icon: Pencil }
            ]).map((item) => {
              const ActiveIcon = item.icon
              const active = mode === item.value
              return (
                <button key={item.value} type="button" onClick={() => switchMode(item.value)} aria-label={item.label} title={item.label} aria-pressed={active} style={{
                  width: '40px', height: '40px', padding: 0, borderRadius: '11px', border: 'none',
                  background: active ? '#E98E6B' : surface, color: active ? '#fff' : text,
                  display: 'grid', placeItems: 'center', cursor: 'pointer'
                }}><ActiveIcon size={18} /></button>
              )
            })}
          </div>
          <div style={{ display: 'flex', gap: '6px' }}>
            {mode === 'crop' && (
              <button type="button" disabled={!canCrop} onClick={() => void applyCrop()} aria-label="应用裁切" title="应用裁切" style={{
                width: '40px', height: '40px', padding: 0, borderRadius: '11px', border: 'none',
                background: canCrop ? '#716E85' : surface, color: canCrop ? '#fff' : muted,
                display: 'grid', placeItems: 'center', cursor: canCrop ? 'pointer' : 'default'
              }}><Check size={18} /></button>
            )}
            <button type="button" onClick={undo} disabled={history.length <= 1} aria-label="撤销" style={{
              width: '40px', height: '40px', borderRadius: '11px', border: 'none', background: surface,
              color: history.length <= 1 ? muted : text, display: 'grid', placeItems: 'center',
              cursor: history.length <= 1 ? 'default' : 'pointer'
            }}><Undo2 size={17} /></button>
            <button type="button" onClick={reset} aria-label="恢复原图" style={{
              width: '40px', height: '40px', borderRadius: '11px', border: 'none', background: surface,
              color: text, display: 'grid', placeItems: 'center', cursor: 'pointer'
            }}><RotateCcw size={17} /></button>
          </div>
        </div>
      </div>
    </div>
  )
}
