import { settingsStore } from '../stores/settingsStore'
import { DEFAULT_API_BASE_URL } from '../config'

function getBase(): string {
  return settingsStore.getState().baseUrl || DEFAULT_API_BASE_URL
}

export function resolveNoteAsset(path?: string | null): string {
  if (!path) return ''
  if (path.startsWith('http')) return path
  return `${getBase()}${path}`
}

export interface ImageNoteSummary {
  id: number
  title: string
  body?: string
  tags: string[]
  created_at: string
  updated_at: string
  cover?: string | null
  cover_width?: number | null
  cover_height?: number | null
}

export interface ImageNoteImage {
  id: number
  url: string
  thumb_url: string
  width?: number
  height?: number
}

export interface ImageNoteComment {
  id: number
  author: 'user' | 'mav'
  content: string
  created_at: string
}

export interface ImageNoteDetail extends ImageNoteSummary {
  images: ImageNoteImage[]
  comments: ImageNoteComment[]
}

export interface NoteSearchResult {
  id: number
  title: string
  created_at: string
  snippet: string
  cover?: string | null
}

export async function listImageNotes(tag = ''): Promise<ImageNoteSummary[]> {
  const url = new URL(`${getBase()}/api/notes`)
  if (tag) url.searchParams.set('tag', tag)
  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`获取笔记失败: ${res.status}`)
  const data = await res.json()
  return data.notes || []
}

export async function listNoteTags(): Promise<string[]> {
  const res = await fetch(`${getBase()}/api/notes/tags`)
  if (!res.ok) throw new Error(`获取标签失败: ${res.status}`)
  const data = await res.json()
  return data.tags || []
}

export async function getImageNote(noteId: number): Promise<ImageNoteDetail> {
  const res = await fetch(`${getBase()}/api/notes/${noteId}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`获取笔记失败: ${res.status}`)
  return res.json()
}

export async function saveImageNote(payload: {
  id?: number
  title: string
  body: string
  tags: string[]
  images: File[]
}): Promise<ImageNoteDetail> {
  const form = new FormData()
  form.append('title', payload.title)
  form.append('body', payload.body)
  form.append('tags', JSON.stringify(payload.tags))
  payload.images.forEach(file => form.append('images', file))

  const endpoint = payload.id
    ? `${getBase()}/api/notes/${payload.id}/update`
    : `${getBase()}/api/notes`
  const res = await fetch(endpoint, {
    method: 'POST',
    body: form
  })
  if (!res.ok) throw new Error(`保存笔记失败: ${res.status}`)
  return res.json()
}

export async function deleteImageNote(noteId: number): Promise<void> {
  const res = await fetch(`${getBase()}/api/notes/${noteId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除笔记失败: ${res.status}`)
}

export async function searchImageNotes(query: string): Promise<NoteSearchResult[]> {
  const url = new URL(`${getBase()}/api/notes/search`)
  url.searchParams.set('q', query)
  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`搜索失败: ${res.status}`)
  const data = await res.json()
  return data.results || []
}

export interface MavNoteCommentJob {
  id: number
  note_id: number
  mode: 'new' | 'regenerate' | 'supplement' | 'reply'
  user_reply: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'interrupted'
  draft_text: string
  comment_id: number | null
  error: string
  created_at: string
  updated_at: string
}

export async function startMavNoteComment(
  noteId: number,
  payload: { mode: 'new' | 'regenerate' | 'supplement' | 'reply', user_reply?: string, request_id: string }
): Promise<MavNoteCommentJob> {
  const res = await fetch(`${getBase()}/api/notes/${noteId}/comment-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  if (!res.ok) throw new Error(`评论启动失败: ${res.status}`)
  return res.json()
}

export async function getLatestMavNoteCommentJob(noteId: number): Promise<MavNoteCommentJob | null> {
  const res = await fetch(`${getBase()}/api/notes/${noteId}/comment-job`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`读取评论进度失败: ${res.status}`)
  const data = await res.json()
  return data.job || null
}

export async function getMavNoteCommentJob(noteId: number, jobId: number): Promise<MavNoteCommentJob> {
  const res = await fetch(`${getBase()}/api/notes/${noteId}/comment-jobs/${jobId}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`读取评论进度失败: ${res.status}`)
  return res.json()
}
