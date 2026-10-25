
import type { Conversation, ChatMessage } from '../types'

const STORAGE_KEY = 'omnipilot_conversations'

// ========== 读取全部会话 ==========
export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

// ========== 保存全部会话 ==========
function saveConversations(convs: Conversation[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(convs))
}

// ========== 新建会话 ==========
export function createConversation(): Conversation {
  const conv: Conversation = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
    title: '新会话',
    messages: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    pinned: false,
  }
  const convs = loadConversations()
  convs.unshift(conv)
  saveConversations(convs)
  return conv
}

// ========== 更新会话 ==========
export function updateConversation(id: string, update: Partial<Conversation>): void {
  const convs = loadConversations()
  const idx = convs.findIndex(c => c.id === id)
  if (idx !== -1) {
    convs[idx] = { ...convs[idx], ...update, updated_at: new Date().toISOString() }
    saveConversations(convs)
  }
}

// ========== 删除会话 ==========
export function deleteConversation(id: string): void {
  const convs = loadConversations().filter(c => c.id !== id)
  saveConversations(convs)
}

// ========== 添加消息 ==========
export function addMessage(convId: string, msg: ChatMessage): void {
  const convs = loadConversations()
  const conv = convs.find(c => c.id === convId)
  if (conv) {
    conv.messages.push(msg)
    // 自动生成标题：用第一条用户消息
    if (conv.title === '新会话' && msg.role === 'user') {
      conv.title = msg.content.slice(0, 30) + (msg.content.length > 30 ? '...' : '')
    }
    conv.updated_at = new Date().toISOString()
    saveConversations(convs)
  }
}

// ========== 更新最后一条消息（用于流式更新） ==========
export function updateLastMessage(convId: string, update: Partial<ChatMessage>): void {
  const convs = loadConversations()
  const conv = convs.find(c => c.id === convId)
  if (conv && conv.messages.length > 0) {
    conv.messages[conv.messages.length - 1] = {
      ...conv.messages[conv.messages.length - 1],
      ...update,
    }
    conv.updated_at = new Date().toISOString()
    saveConversations(convs)
  }
}

// ========== 置顶切换 ==========
export function togglePin(id: string): void {
  const convs = loadConversations()
  const idx = convs.findIndex(c => c.id === id)
  if (idx !== -1) {
    convs[idx].pinned = !convs[idx].pinned
    saveConversations(convs)
  }
}

// ========== 分组（置顶 / 今天 / 7天内 / 更早） ==========
export function groupConversations(convs: Conversation[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000)

  return {
    pinned: convs.filter(c => c.pinned),
    today: convs.filter(c => {
      if (c.pinned) return false
      const d = new Date(c.created_at)
      return d >= today
    }),
    week: convs.filter(c => {
      if (c.pinned) return false
      const d = new Date(c.created_at)
      return d >= weekAgo && d < today
    }),
    older: convs.filter(c => {
      if (c.pinned) return false
      const d = new Date(c.created_at)
      return d < weekAgo
    }),
  }
}