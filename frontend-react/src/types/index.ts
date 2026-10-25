// ========== 消息类型 ==========
export interface SourceItem {
  file_name: string
  score: number
  page: number
  section: string
  preview: string
  full_text: string
}

export interface TimingInfo {
  total: number
  nodes: Record<string, number>
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: SourceItem[]
  thinking?: string
  timing?: TimingInfo
}

// ========== 会话类型 ==========
export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  created_at: string
  updated_at: string
  pinned: boolean
}

// ========== SSE 事件类型 ==========
export type SSEEvent =
  | { type: 'thinking_token'; content: string }
  | { type: 'answer'; content: string }
  | { type: 'sources'; sources: SourceItem[] }
  | { type: 'timing'; timing: TimingInfo }
  | { type: 'error'; message: string }
  | { type: 'done' }

// ========== 文档类型 ==========
export interface DocInfo {
  id?: string
  name: string
  type: 'pdf' | 'word' | 'excel'
  path: string
  scope?: 'public' | 'personal'
  size?: number
  modified?: number
  chunk_count?: number
  status?: string
}

// ========== 用户与权限类型 ==========
export type UserRole = 'user' | 'admin'

export interface AuthUser {
  username: string
  role: UserRole
  department?: string | null
  full_name?: string | null
}

export interface LoginResponse extends AuthUser {
  access_token: string
  token_type: string
}

export interface RegisterPayload {
  username: string
  password: string
  confirm_password: string
  full_name?: string
  department?: string
  email?: string
}

export interface PublicUser extends AuthUser {
  email?: string | null
  is_active: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface AdminUserCreatePayload extends RegisterPayload {
  role?: UserRole
  is_active?: boolean
}

export interface AdminUserUpdatePayload {
  password?: string
  confirm_password?: string
  role?: UserRole
  full_name?: string
  department?: string
  email?: string
  is_active?: boolean
}
