import { settingsStore } from '../stores/settingsStore'
import { DEFAULT_API_BASE_URL } from '../config'

function getBase(): string {
  return settingsStore.getState().baseUrl || DEFAULT_API_BASE_URL
}

// ============ 书架相关 ============

export const getBookList = async () => {
  const res = await fetch(`${getBase()}/api/reading/books`)
  if (!res.ok) throw new Error(`获取书架失败: ${res.status}`)
  return res.json() // { books: [...] }
}

export const uploadBook = async (
  file: File,
  onProgress?: (progress: number) => void
) => {
  const form = new FormData()
  form.append('file', file)

  // 用XHR以便监听上传进度
  return new Promise<any>((resolve, reject) => {
    const xhr = new XMLHttpRequest()

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }

    xhr.onload = () => {
      if (xhr.status === 200) {
        resolve(JSON.parse(xhr.responseText))
      } else {
        reject(new Error(`上传失败: ${xhr.status}`))
      }
    }

    xhr.onerror = () => reject(new Error('网络错误'))

    xhr.open('POST', `${getBase()}/api/reading/books/upload`)
    xhr.send(form)
  })
}

export const deleteBook = async (bookId: number) => {
  const res = await fetch(`${getBase()}/api/reading/books/${bookId}`, {
    method: 'DELETE'
  })
  if (!res.ok) throw new Error(`删除失败: ${res.status}`)
}

// ============ 处理相关 ============

export const processBook = async (bookId: number) => {
  const res = await fetch(`${getBase()}/api/reading/books/${bookId}/process`, {
    method: 'POST'
  })
  if (!res.ok) throw new Error(`触发处理失败: ${res.status}`)
  return res.json()
}

export const getBookStatus = async (bookId: number) => {
  const res = await fetch(`${getBase()}/api/reading/books/${bookId}/status`)
  if (!res.ok) throw new Error(`获取状态失败: ${res.status}`)
  return res.json() // { status, processed_chapters, total_chapters }
}

// ============ 阅读相关 ============

export const listChapters = async (bookId: number) => {
  const res = await fetch(`${getBase()}/api/reading/books/${bookId}/chapters`)
  if (!res.ok) throw new Error(`获取章节失败: ${res.status}`)
  return res.json() // { chapters: [{chapter_index, title}] }
}

export const getChapter = async (bookId: number, chapterIndex: number) => {
  const res = await fetch(`${getBase()}/api/reading/books/${bookId}/chapters/${chapterIndex}`)
  if (!res.ok) throw new Error(`获取章节内容失败: ${res.status}`)
  return res.json() // { chapter_index, title, content_text, content_images, annotations }
}

export const addAnnotation = (bookId: number, payload: {
  chapter_index: number
  start_char: number
  end_char: number
  quote_text: string
  content: string
}) => {
  return fetch(`${getBase()}/api/reading/books/${bookId}/annotations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  // 返回SSE流，调用方自己处理reader
}

export const addReply = (annotationId: number, content: string) => {
  return fetch(`${getBase()}/api/reading/annotations/${annotationId}/replies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  })
  // 返回SSE流
}

export const updateProgress = async (bookId: number, lastChapter: number, lastPosition: number) => {
  await fetch(`${getBase()}/api/reading/books/${bookId}/progress`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ last_chapter: lastChapter, last_position: lastPosition })
  })
}

// ============ 辅助函数 ============

export const isSupportedBookFormat = (file: File): boolean => {
  return file.name.toLowerCase().endsWith('.epub')
}

export const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

export const getBookStatusText = (status: string): string => {
  const map: Record<string, string> = {
    'unprocessed': '未处理',
    'processing': 'Mav阅读中',
    'ready': '可阅读',
    'failed': '处理失败'
  }
  return map[status] || status
}

export const canOpenBook = (status: string): boolean => {
  return status === 'ready'
}
