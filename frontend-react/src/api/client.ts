
import type {
  SSEEvent,
  DocInfo,
  LoginResponse,
  RegisterPayload,
  PublicUser,
  AdminUserCreatePayload,
  AdminUserUpdatePayload,
} from '../types'

const BASE = ''

async function readError(resp: Response, fallback: string): Promise<string> {
  try {
    const data = await resp.json()
    return data.detail || data.message || fallback
  } catch {
    return fallback
  }
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` }
}

// ========== 登录 ==========
export async function login(username: string, password: string): Promise<LoginResponse> {
  const formData = new URLSearchParams()
  formData.append('username', username)
  formData.append('password', password)

  const resp = await fetch(`${BASE}/token`, {
    method: 'POST',
    body: formData,
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `登录失败 (${resp.status})`))
  }
  return await resp.json()
}

// ========== 注册 ==========
export async function registerUser(payload: RegisterPayload): Promise<PublicUser> {
  const resp = await fetch(`${BASE}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `注册失败 (${resp.status})`))
  }
  return await resp.json()
}

// ========== 管理员用户管理 ==========
export async function listAdminUsers(token: string): Promise<PublicUser[]> {
  const resp = await fetch(`${BASE}/admin/users`, {
    headers: authHeaders(token),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `获取用户列表失败 (${resp.status})`))
  }
  return await resp.json()
}

export async function createAdminUser(token: string, payload: AdminUserCreatePayload): Promise<PublicUser> {
  const resp = await fetch(`${BASE}/admin/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `新增用户失败 (${resp.status})`))
  }
  return await resp.json()
}

export async function updateAdminUser(
  token: string,
  username: string,
  payload: AdminUserUpdatePayload
): Promise<PublicUser> {
  const resp = await fetch(`${BASE}/admin/users/${encodeURIComponent(username)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `更新用户失败 (${resp.status})`))
  }
  return await resp.json()
}

export async function deleteAdminUser(token: string, username: string): Promise<void> {
  const resp = await fetch(`${BASE}/admin/users/${encodeURIComponent(username)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `删除用户失败 (${resp.status})`))
  }
}

export type KnowledgeScope = 'public' | 'personal'

type KnowledgeDocs = Record<'pdf' | 'word' | 'excel', DocInfo[]>

interface KnowledgeUploadResult {
  status: string
  scope: KnowledgeScope
  total_chunks: number
  details: Record<string, number>
  documents?: DocInfo[]
}

// ========== 公共知识库 ==========
export async function fetchPublicKnowledgeDocs(token: string): Promise<KnowledgeDocs> {
  const resp = await fetch(`${BASE}/knowledge/docs`, {
    headers: authHeaders(token),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `获取公共知识库失败 (${resp.status})`))
  }
  return await resp.json()
}

export async function uploadPublicKnowledgeFiles(token: string, files: File[]): Promise<KnowledgeUploadResult> {
  const formData = new FormData()
  files.forEach(file => formData.append('files', file))
  const resp = await fetch(`${BASE}/knowledge/upload`, {
    method: 'POST',
    headers: authHeaders(token),
    body: formData,
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `导入公共知识库失败 (${resp.status})`))
  }
  return await resp.json()
}

// ========== 个人知识库 ==========
export async function fetchPersonalKnowledgeDocs(token: string): Promise<KnowledgeDocs> {
  const resp = await fetch(`${BASE}/knowledge/personal/docs`, {
    headers: authHeaders(token),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `获取个人知识库失败 (${resp.status})`))
  }
  return await resp.json()
}

export async function uploadPersonalKnowledgeFiles(token: string, files: File[]): Promise<KnowledgeUploadResult> {
  const formData = new FormData()
  files.forEach(file => formData.append('files', file))
  const resp = await fetch(`${BASE}/knowledge/personal/upload`, {
    method: 'POST',
    headers: authHeaders(token),
    body: formData,
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `导入个人知识库失败 (${resp.status})`))
  }
  return await resp.json()
}

export async function deletePersonalKnowledgeDoc(token: string, documentId: string): Promise<void> {
  const resp = await fetch(`${BASE}/knowledge/personal/docs/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  })
  if (!resp.ok) {
    throw new Error(await readError(resp, `删除个人知识库文件失败 (${resp.status})`))
  }
}

// ========== 流式聊天 ==========
export async function* streamChat(
  messages: { role: string; content: string }[],
  token: string
): AsyncGenerator<SSEEvent> {
  const resp = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ messages }),
  })

  if (!resp.ok) throw new Error(`请求失败 (${resp.status})`)
  if (!resp.body) throw new Error('响应无 body')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event = JSON.parse(line.slice(6)) as SSEEvent
          yield event
        } catch { /* 忽略解析失败 */ }
      }
    }
  }
}

// ========== 获取文档列表 ==========
export async function fetchDocList(type: 'pdf' | 'word' | 'excel'): Promise<DocInfo[]> {
  const resp = await fetch(`${BASE}/files/${type}/`)
  if (!resp.ok) return []
  // 后端没有直接的文件列表 API，这里返回空，由前端硬编码或在知识库导入时构建
  return []
}

// ========== 文件 URL 构建 ==========
export function getFileUrl(fileName: string): string {
  const ext = fileName.toLowerCase().split('.').pop() || ''
  const subdirMap: Record<string, string> = {
    pdf: 'pdf', doc: 'word/doc', docx: 'word/docx',
    xls: 'excel', xlsx: 'excel',
  }
  const subdir = subdirMap[ext] || ''
  return `${BASE}/files/${subdir}/${fileName}`
}