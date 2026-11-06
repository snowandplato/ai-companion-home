import { useEffect, useState, type ReactNode } from 'react'
import './AssistantMessageSegments.css'

export type AssistantMessageStyle = 'prose' | 'bubbles'

type Segment = { kind: 'text' | 'search' | 'memoryPending' | 'memoryDone'; text: string; complete: boolean }

const noticePattern = /(\[\[mav_search_used\]\]|\[\[mav_memory_recall_pending\]\]|\[\[mav_memory_recall_done\]\])/g

function noticeKind(part: string): Segment['kind'] | null {
  if (part === '[[mav_search_used]]') return 'search'
  if (part === '[[mav_memory_recall_pending]]') return 'memoryPending'
  if (part === '[[mav_memory_recall_done]]') return 'memoryDone'
  return null
}

function splitParagraphs(text: string, final: boolean): Segment[] {
  const segments: Segment[] = []
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  let buffer = ''
  let inFence = false

  const push = (complete: boolean) => {
    const value = buffer.trim()
    if (value) segments.push({ kind: 'text', text: value, complete })
    buffer = ''
  }

  for (const line of lines) {
    if (!inFence && !line.trim() && buffer.trim()) {
      push(true)
      continue
    }
    buffer += (buffer ? '\n' : '') + line
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence

    // A long unbroken paragraph should not hold the first bubble indefinitely.
    if (!inFence && !buffer.includes('\n') && buffer.length > 240) {
      const match = [...buffer.matchAll(/[。！？!?；;](?:[”’」』])?/g)]
        .find((item) => (item.index ?? 0) >= 120)
      if (match && match.index !== undefined) {
        const end = match.index + match[0].length
        const remaining = buffer.slice(end).trimStart()
        buffer = buffer.slice(0, end)
        push(true)
        buffer = remaining
      }
    }
  }
  push(final)
  return segments
}

function splitAssistantSegments(content: string, streaming: boolean): Segment[] {
  const portions = content.split(noticePattern)
  const segments: Segment[] = []
  portions.forEach((portion, index) => {
    const kind = noticeKind(portion)
    if (kind) segments.push({ kind, text: '', complete: true })
    else segments.push(...splitParagraphs(portion, !streaming || index < portions.length - 1))
  })
  return segments
}

interface Props {
  content: string
  styleMode: AssistantMessageStyle
  streaming: boolean
  dark: boolean
  renderText: (text: string) => ReactNode
  searchNotice: ReactNode
  memoryPendingNotice: ReactNode
  memoryDoneNotice: ReactNode
}

export function AssistantMessageSegments({ content, styleMode, streaming, dark, renderText, searchNotice, memoryPendingNotice, memoryDoneNotice }: Props) {
  const segments = splitAssistantSegments(content, streaming)
  const [wasStreamed] = useState(streaming)
  const [visibleCount, setVisibleCount] = useState(() => streaming ? 0 : Number.POSITIVE_INFINITY)
  const availableCount = streaming
    ? segments.findIndex((segment) => !segment.complete)
    : segments.length
  const readyCount = availableCount < 0 ? segments.length : availableCount
  const nextIsNotice = segments[visibleCount]?.kind !== 'text'

  useEffect(() => {
    if (styleMode !== 'bubbles' || !wasStreamed || visibleCount >= readyCount) return
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const timer = window.setTimeout(
      () => setVisibleCount((count) => Math.min(count + 1, readyCount)),
      reducedMotion || nextIsNotice ? 0 : visibleCount === 0 ? 100 : 280,
    )
    return () => window.clearTimeout(timer)
  }, [styleMode, wasStreamed, visibleCount, readyCount, nextIsNotice])

  if (styleMode === 'prose') {
    return <>{content.split(noticePattern).map((part, index) => {
      const kind = noticeKind(part)
      return <div key={index}>
        {kind === 'search' ? searchNotice
          : kind === 'memoryPending' ? memoryPendingNotice
          : kind === 'memoryDone' ? memoryDoneNotice
          : part.trim() ? renderText(part.trim()) : null}
      </div>
    })}</>
  }

  const shown = wasStreamed ? segments.slice(0, visibleCount) : segments
  return <div className={`mav-bubble-stack${dark ? ' mav-bubble-stack-dark' : ''}${wasStreamed ? '' : ' mav-bubble-stack-static'}`}>
    {shown.map((segment, index) => segment.kind === 'text'
      ? <div key={index} className="mav-assistant-bubble">{renderText(segment.text)}</div>
      : <div key={index} className="mav-bubble-notice" role={segment.kind === 'memoryPending' ? 'status' : undefined}>
          {segment.kind === 'search' ? searchNotice
            : segment.kind === 'memoryPending' ? memoryPendingNotice : memoryDoneNotice}
        </div>
    )}
  </div>
}
