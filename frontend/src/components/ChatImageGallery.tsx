import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, Minus, Plus, RotateCcw, X } from 'lucide-react'
import type { MessageImage } from '../services/api'

interface ChatImageGalleryProps {
  images?: MessageImage[]
  legacyUrl?: string | null
  dark?: boolean
}

interface GalleryItem {
  id: string
  url: string
  thumbUrl: string
  summary?: string
}

export function ChatImageGallery({ images = [], legacyUrl, dark = false }: ChatImageGalleryProps) {
  const items: GalleryItem[] = images.length
    ? images.map((image) => ({
        id: image.id,
        url: image.url,
        thumbUrl: image.thumbUrl || image.url,
        summary: image.visionSummary
      }))
    : legacyUrl
      ? [{ id: 'legacy', url: legacyUrl, thumbUrl: legacyUrl }]
      : []
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const [scale, setScale] = useState(1)
  const pointerStart = useRef<{ x: number; y: number } | null>(null)

  const close = () => {
    setActiveIndex(null)
    setScale(1)
  }
  const move = (direction: number) => {
    if (activeIndex === null || items.length < 2) return
    setActiveIndex((activeIndex + direction + items.length) % items.length)
    setScale(1)
  }

  useEffect(() => {
    if (activeIndex === null) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
      if (event.key === 'ArrowLeft') move(-1)
      if (event.key === 'ArrowRight') move(1)
      if (event.key === '+' || event.key === '=') setScale((value) => Math.min(4, value + 0.25))
      if (event.key === '-') setScale((value) => Math.max(1, value - 0.25))
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [activeIndex, items.length])

  if (!items.length) return null

  const columns = items.length === 1 ? '1fr' : 'repeat(2, minmax(0, 1fr))'
  const maxHeight = items.length === 1 ? 360 : 280
  const controlStyle = {
    width: 38,
    height: 38,
    borderRadius: 11,
    border: '1px solid rgba(255,255,255,.16)',
    color: '#fff',
    background: 'rgba(25,23,31,.64)',
    display: 'grid',
    placeItems: 'center',
    cursor: 'pointer'
  } as const

  return (
    <>
      <div
        className="chat-image-gallery"
        style={{
          display: 'grid',
          gridTemplateColumns: columns,
          gap: 5,
          width: 'min(100%, 430px)',
          maxHeight,
          overflow: 'hidden',
          borderRadius: 13,
          marginBottom: 8,
          background: dark ? '#302D38' : '#ECE9F0'
        }}
      >
        {items.map((item, index) => (
          <button
            key={item.id}
            type="button"
            aria-label={`查看第${index + 1}张图片大图`}
            onClick={() => setActiveIndex(index)}
            style={{
              border: 0,
              padding: 0,
              minWidth: 0,
              overflow: 'hidden',
              cursor: 'zoom-in',
              background: 'transparent',
              gridRow: items.length === 3 && index === 0 ? 'span 2' : undefined
            }}
          >
            <img
              src={item.thumbUrl}
              alt={item.summary || `对话图片 ${index + 1}`}
              loading="lazy"
              style={{
                width: '100%',
                height: items.length === 1 ? 'auto' : items.length === 3 && index === 0 ? 280 : 138,
                maxHeight: items.length === 1 ? 360 : undefined,
                minHeight: items.length === 1 ? 120 : 138,
                objectFit: items.length === 1 ? 'contain' : 'cover',
                display: 'block'
              }}
            />
          </button>
        ))}
      </div>

      {activeIndex !== null && createPortal(
        <div
          role="dialog"
          aria-modal="true"
          aria-label="图片查看器"
          onClick={close}
          onPointerDown={(event) => { pointerStart.current = { x: event.clientX, y: event.clientY } }}
          onPointerUp={(event) => {
            const start = pointerStart.current
            pointerStart.current = null
            if (!start || scale > 1) return
            const dx = event.clientX - start.x
            const dy = event.clientY - start.y
            if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) move(dx < 0 ? 1 : -1)
          }}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 5000,
            background: 'rgba(17,15,22,.94)',
            backdropFilter: 'blur(12px)',
            display: 'grid',
            placeItems: 'center',
            touchAction: scale > 1 ? 'pan-x pan-y' : 'none'
          }}
        >
          <img
            src={items[activeIndex].url}
            alt={items[activeIndex].summary || `对话图片 ${activeIndex + 1}`}
            onClick={(event) => event.stopPropagation()}
            onDoubleClick={() => setScale((value) => value === 1 ? 2 : 1)}
            draggable={false}
            style={{
              maxWidth: '94vw',
              maxHeight: '84dvh',
              objectFit: 'contain',
              transform: `scale(${scale})`,
              transition: 'transform 180ms ease',
              userSelect: 'none'
            }}
          />

          <button type="button" aria-label="关闭大图" onClick={close} style={{ ...controlStyle, position: 'absolute', top: 18, right: 18 }}>
            <X size={20} />
          </button>

          {items.length > 1 && (
            <>
              <button type="button" aria-label="上一张" onClick={(event) => { event.stopPropagation(); move(-1) }} style={{ ...controlStyle, position: 'absolute', left: 18, top: '50%' }}>
                <ChevronLeft size={22} />
              </button>
              <button type="button" aria-label="下一张" onClick={(event) => { event.stopPropagation(); move(1) }} style={{ ...controlStyle, position: 'absolute', right: 18, top: '50%' }}>
                <ChevronRight size={22} />
              </button>
            </>
          )}

          <div
            onClick={(event) => event.stopPropagation()}
            style={{
              position: 'absolute',
              bottom: 18,
              display: 'flex',
              alignItems: 'center',
              gap: 7,
              padding: '7px 9px',
              borderRadius: 13,
              background: 'rgba(25,23,31,.7)',
              border: '1px solid rgba(255,255,255,.13)'
            }}
          >
            <span style={{ color: 'rgba(255,255,255,.82)', fontSize: 13, minWidth: 42, textAlign: 'center' }}>
              {activeIndex + 1} / {items.length}
            </span>
            <button type="button" aria-label="缩小" onClick={() => setScale((value) => Math.max(1, value - 0.25))} style={{ ...controlStyle, width: 32, height: 32 }}><Minus size={16} /></button>
            <button type="button" aria-label="恢复原始大小" onClick={() => setScale(1)} style={{ ...controlStyle, width: 32, height: 32 }}><RotateCcw size={15} /></button>
            <button type="button" aria-label="放大" onClick={() => setScale((value) => Math.min(4, value + 0.25))} style={{ ...controlStyle, width: 32, height: 32 }}><Plus size={16} /></button>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
