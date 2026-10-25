
import { useState, useRef, useEffect } from 'react'
import ModelSwitcher from './ModelSwitcher'
import TestPanel from './TestPanel'
import ThemeToggle from './ThemeToggle'
import ReactMarkdown from 'react-markdown'
import { useHotkeys } from 'react-hotkeys-hook'
import type { ChatMessage, SourceItem, TimingInfo, DocInfo } from '../types'
import { streamChat, getFileUrl } from '../api/client'
import { loadConversations, addMessage, updateLastMessage } from '../store/sessionStore'

const BASE = ''

interface TestResult { index: number; total: number; qid: string; correct: boolean; answer: string; expected: string; elapsed: number; difficulty: string; error?: string; question?: string; options?: Record<string, string>; timing?: Record<string, number> }
interface TestSummary { total: number; correct: number; wrong: number; errors: number; accuracy: number; avg_time: number; total_time: number; wrong_details: { id: string; question: string; expected: string; got: string; difficulty: string; source: string; reply_preview: string }[]; difficulty_stats: Record<string, { total: number; correct: number }> }

interface Props {
  token: string
  activeId: string | null
  selectedDocs: DocInfo[]
  onNewConv: () => void
  onRefresh: () => void
  onRemoveDoc: (doc: DocInfo) => void
  onClearDocs: () => void
  testResults: TestResult[]
  testSummary: TestSummary | null
  testRunning: boolean
  testProgress: number
  testDataset: string
  onStopTest: () => void
  testStopped: boolean
  onContinueTest: () => void
  onRunTest: (dataset: string, startQid?: string, testId?: string) => void
  theme: 'light' | 'dark'
  onToggleTheme: () => void
  testHistory: {
    id: string
    dataset: string
    startTime: string
    endTime?: string
    status: 'running' | 'completed' | 'stopped'
    progress: number
    total: number
    correct: number
    wrong: number
    errors: number
    currentQid?: string
    nextQid?: string
  }[]
  testCurrentQid: string
  testNextQid: string
}

export default function ChatView({ token, activeId, selectedDocs, onNewConv, onRefresh, onRemoveDoc, onClearDocs, testResults, testSummary, testRunning, testProgress, testDataset, onStopTest, testStopped, onContinueTest, onRunTest, theme, onToggleTheme, testHistory, testCurrentQid, testNextQid }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState<{
    thinking: string
    answer: string
    sources: SourceItem[]
    timing: TimingInfo | null
  } | null>(null)
  const [expandedThinking, setExpandedThinking] = useState<number[]>([])
  const [expandedSources, setExpandedSources] = useState<number[]>([])
  const [stats, setStats] = useState<{count: number, update_time: string, status: string} | null>(null)
  const [showShortcuts, setShowShortcuts] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [currentModel, setCurrentModel] = useState<string>('DeepSeek (deepseek-v4-flash) 【已配置api-key】')

  // 加载当前会话消息
  useEffect(() => {
    if (activeId) {
      const convs = loadConversations()
      const conv = convs.find(c => c.id === activeId)
      setMessages(conv?.messages || [])
      setExpandedThinking([])
      setExpandedSources([])
    } else {
      setMessages([])
    }
  }, [activeId])
  // 新增：获取当前模型设置
  const refreshCurrentModel = () => {
    if (!token) return
    fetch('/settings', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.text())
      .then(text => {
        try {
          const data = JSON.parse(text)
          const providerLabels: Record<string, string> = {
            deepseek: 'DeepSeek',
            doubao: '豆包',
            minimax: 'Minimax',
            kimi: 'Kimi',
            other: '其他模型',
          }
          const providerName = providerLabels[data.provider] || data.provider
          const modelName = data.model.split('/').pop() || data.model
          
          // 检查API Key是否已配置
          const apiKeyConfigured = data.api_key || data.api_keys?.[data.provider]
          let statusText = ''
          if (apiKeyConfigured) {
            statusText = '【已配置api-key】'
          } else {
            statusText = '【未配置api-key，前往系统设置进行配置】'
          }
          
          setCurrentModel(`${providerName} (${modelName}) ${statusText}`)
        } catch (parseError) {
          console.error('解析JSON失败:', parseError)
        }
      })
      .catch(error => {
        console.error('获取模型配置失败:', error)
      })
  }
  
  useEffect(() => {
    refreshCurrentModel()
  }, [token])
  
  // 监听hash变化，当从设置页面返回时刷新模型
  useEffect(() => {
    const handleHashChange = () => {
      if (window.location.hash !== '#settings') {
        console.log('从设置页面返回，刷新模型')
        refreshCurrentModel()
      }
    }
    
    window.addEventListener('hashchange', handleHashChange)
    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [token])
  
  // 监听storage变化，当设置页面更新配置时同步更新
  useEffect(() => {
    const handleStorageChange = () => {
      console.log('storage变化，刷新模型')
      refreshCurrentModel()
    }
    
    window.addEventListener('storage', handleStorageChange)
    return () => {
      window.removeEventListener('storage', handleStorageChange)
    }
  }, [token])
  
  // 定期刷新当前模型，确保显示最新的模型配置
  useEffect(() => {
    if (!token) return
    const interval = setInterval(() => {
      console.log('定期刷新模型')
      refreshCurrentModel()
    }, 2000) // 每2秒刷新一次
    
    return () => clearInterval(interval)
  }, [token])
  
  // 监听storage变化，当设置页面更新配置时同步更新
  useEffect(() => {
    const handleStorageChange = () => {
      refreshCurrentModel()
    }
    
    window.addEventListener('storage', handleStorageChange)
    return () => {
      window.removeEventListener('storage', handleStorageChange)
    }
  }, [token])
  // 自动滚动
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, streaming])

  // 获取知识库统计
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const resp = await fetch(`${BASE}/knowledge/stats`)
        if (resp.ok) setStats(await resp.json())
      } catch { /* 忽略 */ }
    }
    fetchStats()
    const interval = setInterval(fetchStats, 30000)
    return () => clearInterval(interval)
  }, [])
  
  // 定期刷新当前模型，确保显示最新的模型配置
  useEffect(() => {
    if (!token) return
    const interval = setInterval(() => {
      refreshCurrentModel()
    }, 2000) // 每2秒刷新一次
    
    return () => clearInterval(interval)
  }, [token])

  // 自动调整输入框高度
  const adjustHeight = () => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 150) + 'px'
    }
  }

  const handleSend = async () => {
    const trimmed = input.trim()
    if (!trimmed || loading || !activeId) return

    // 拼接文档引用
    let finalInput = trimmed
    if (selectedDocs.length > 0) {
      finalInput = selectedDocs.map(d => `@[${d.name}]`).join(' ') + ' ' + finalInput
    }

    const userMsg: ChatMessage = { role: 'user', content: finalInput }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    addMessage(activeId, userMsg)
    setInput('')
    setLoading(true)
    
    const assistantMsg: ChatMessage = { role: 'assistant', content: '' }
    setMessages(prev => [...prev, assistantMsg])
    addMessage(activeId, assistantMsg)

    setStreaming({ thinking: '', answer: '', sources: [], timing: null })

    try {
      const payload = newMessages.map(m => ({ role: m.role, content: m.content }))
      let thinking = ''
      let answer = ''
      let sources: SourceItem[] = []
      let timing: TimingInfo | null = null

      for await (const event of streamChat(payload, token)) {
        if (event.type === 'thinking_token') {
          thinking += event.content
          setStreaming({ thinking, answer, sources, timing })
        } else if (event.type === 'answer') {
          answer = event.content
          setStreaming({ thinking, answer, sources, timing })
        } else if (event.type === 'sources') {
          sources = event.sources
          setStreaming({ thinking, answer, sources, timing })
        } else if (event.type === 'timing') {
          timing = event.timing
          setStreaming({ thinking, answer, sources, timing })
        } else if (event.type === 'error') {
          answer = `❌ 系统错误: ${event.message}`
          setStreaming({ thinking, answer, sources, timing })
        }
      }

      // 流式结束
      const finalContent = answer || '⚠️ 系统未能生成有效回答'
      updateLastMessage(activeId, {
        content: finalContent,
        thinking: thinking,
        sources: sources,
        timing: timing || undefined,
      })
      setStreaming(null)
      setMessages(prev => {
        const updated = [...prev]
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          content: finalContent,
          thinking: thinking,
          sources: sources,
          timing: timing || undefined,
        }
        return updated
      })
      onRefresh()
    } catch (err: any) {
      const errContent = `❌ 请求失败: ${err.message}`
      updateLastMessage(activeId, { content: errContent })
      setStreaming(null)
      setMessages(prev => {
        const updated = [...prev]
        updated[updated.length - 1] = { ...updated[updated.length - 1], content: errContent }
        return updated
      })
      onRefresh()
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text)
  }

  const handleExportMarkdown = () => {
    const lines: string[] = []
    lines.push(`# 银行业监管制度问答记录\n`)
    lines.push(`> 导出时间: ${new Date().toLocaleString()}\n`)
    messages.forEach((msg, idx) => {
      if (msg.role === 'user') {
        lines.push(`### 🙋 提问 ${Math.floor(idx / 2) + 1}\n`)
        lines.push(msg.content)
        lines.push('')
      } else {
        lines.push(`### 🤖 回答\n`)
        lines.push(msg.content)
        lines.push('')
        if (msg.thinking) {
          lines.push(`<details>\n<summary>💭 思考过程</summary>\n\n${msg.thinking}\n\n</details>\n`)
        }
        if (msg.sources && msg.sources.length > 0) {
          lines.push(`**📚 信息来源:**\n`)
          msg.sources.forEach((src, si) => {
            lines.push(`${si + 1}. ${src.file_name} ${src.score > 0 ? `(⭐${src.score.toFixed(4)})` : ''}`)
            if (src.preview) lines.push(`   > ${src.preview}`)
          })
          lines.push('')
        }
        if (msg.timing) {
          lines.push(`⏱️ 总用时: ${msg.timing.total} 秒\n`)
        }
      }
    })
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `QA对话记录_${new Date().toISOString().slice(0, 10)}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  useHotkeys('ctrl+enter', handleSend, { enableOnFormTags: true })
  useHotkeys('ctrl+n', onNewConv, { enableOnFormTags: true })
  useHotkeys('ctrl+/', (e) => { e.preventDefault(); setShowShortcuts(prev => !prev) }, { enableOnFormTags: true })
  
  // 如果没有激活的会话，显示欢迎界面
  if (!activeId) {
    return (
      <div className="chat-area">
        <div className="chat-header">
          <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
          <div className="chat-header-actions">
            <ThemeToggle theme={theme} onToggleTheme={() => onToggleTheme()} />
          </div>
          {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right', marginRight: 12 }}>
              <span title="知识库状态" style={{ cursor: 'help' }}>{stats.status === 'connected' ? '🟢' : '🔴'}</span>
              {' '}
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
            {' | '}
            <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
          </div>
        )}
      </div>
        <div className="message-list-empty">
          <div style={{ fontSize: 32, marginBottom: 20 }}>👋 欢迎使用</div>
          <div style={{ fontSize: 18, marginBottom: 10, color: 'var(--text-secondary)' }}>面向银行业监管制度与统计报表的可信RAG问答系统</div>
          <div style={{ fontSize: 16, marginBottom: 30, color: 'var(--text-secondary)' }}>专注于银行业监管制度与统计报表的智能问答服务</div>
          <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
            <p>📚 支持查询银行业监管制度文件</p>
            <p>📊 支持统计报表分析与解读</p>
            <p>🤖 基于可信RAG技术，提供准确可靠的回答</p>
          </div>
          <div style={{ marginTop: 40 }}>
            <button
              onClick={onNewConv}
              style={{ padding: '12px 32px', fontSize: 16, backgroundColor: 'var(--primary)', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer' }}
            >
              ➕ 新建会话开始问答
            </button>
          </div>
        </div>
      </div>
    )
  }
  
  // 如果没有激活的会话，显示欢迎界面
  if (!activeId) {
    return (
      <div className="chat-area">
        <div className="chat-header">
          <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
          <div className="chat-header-actions">
            <ThemeToggle theme={theme} onToggleTheme={() => onToggleTheme()} />
          </div>
          {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right', marginRight: 12 }}>
              <span title="知识库状态" style={{ cursor: 'help' }}>{stats.status === 'connected' ? '🟢' : '🔴'}</span>
              {' '}
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
            {' | '}
            <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
          </div>
        )}
      </div>
        <div className="message-list-empty">
          <div style={{ fontSize: 32, marginBottom: 20 }}>👋 欢迎使用</div>
          <div style={{ fontSize: 18, marginBottom: 10, color: 'var(--text-secondary)' }}>面向银行业监管制度与统计报表的可信RAG问答系统</div>
          <div style={{ fontSize: 16, marginBottom: 30, color: 'var(--text-secondary)' }}>专注于银行业监管制度与统计报表的智能问答服务</div>
          <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
            <p>📚 支持查询银行业监管制度文件</p>
            <p>📊 支持统计报表分析与解读</p>
            <p>🤖 基于可信RAG技术，提供准确可靠的回答</p>
          </div>
          <div style={{ marginTop: 40 }}>
            <button
              onClick={onNewConv}
              style={{ padding: '12px 32px', fontSize: 16, backgroundColor: 'var(--primary)', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer' }}
            >
              ➕ 新建会话开始问答
            </button>
          </div>
        </div>
      </div>
    )
  }
  
  // 如果没有激活的会话，显示欢迎界面
  if (!activeId) {
    return (
      <div className="chat-area">
        <div className="chat-header">
          <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
          <div className="chat-header-actions">
            <ThemeToggle theme={theme} onToggleTheme={() => onToggleTheme()} />
          </div>
          {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right', marginRight: 12 }}>
              <span title="知识库状态" style={{ cursor: 'help' }}>{stats.status === 'connected' ? '🟢' : '🔴'}</span>
              {' '}
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
            {' | '}
            <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
          </div>
        )}
      </div>
        <div className="message-list-empty">
          <div style={{ fontSize: 32, marginBottom: 20 }}>👋 欢迎使用</div>
          <div style={{ fontSize: 18, marginBottom: 10, color: 'var(--text-secondary)' }}>面向银行业监管制度与统计报表的可信RAG问答系统</div>
          <div style={{ fontSize: 16, marginBottom: 30, color: 'var(--text-secondary)' }}>专注于银行业监管制度与统计报表的智能问答服务</div>
          <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
            <p>📚 支持查询银行业监管制度文件</p>
            <p>📊 支持统计报表分析与解读</p>
            <p>🤖 基于可信RAG技术，提供准确可靠的回答</p>
          </div>
          <div style={{ marginTop: 40 }}>
            <button
              onClick={onNewConv}
              style={{ padding: '12px 32px', fontSize: 16, backgroundColor: 'var(--primary)', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer' }}
            >
              ➕ 新建会话开始问答
            </button>
          </div>
        </div>
      </div>
    )
  }
  
  // 如果没有激活的会话，显示欢迎界面
  if (!activeId) {
    return (
      <div className="chat-area">
        <div className="chat-header">
          <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
          <div className="chat-header-actions">
            <ThemeToggle theme={theme} onToggleTheme={() => onToggleTheme()} />
          </div>
          {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right', marginRight: 12 }}>
              <span title="知识库状态" style={{ cursor: 'help' }}>{stats.status === 'connected' ? '🟢' : '🔴'}</span>
              {' '}
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
            {' | '}
            <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
          </div>
        )}
      </div>
        <div className="message-list-empty">
          <div style={{ fontSize: 32, marginBottom: 20 }}>👋 欢迎使用</div>
          <div style={{ fontSize: 18, marginBottom: 10, color: 'var(--text-secondary)' }}>面向银行业监管制度与统计报表的可信RAG问答系统</div>
          <div style={{ fontSize: 16, marginBottom: 30, color: 'var(--text-secondary)' }}>专注于银行业监管制度与统计报表的智能问答服务</div>
          <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
            <p>📚 支持查询银行业监管制度文件</p>
            <p>📊 支持统计报表分析与解读</p>
            <p>🤖 基于可信RAG技术，提供准确可靠的回答</p>
          </div>
          <div style={{ marginTop: 40 }}>
            <button
              onClick={onNewConv}
              style={{ padding: '12px 32px', fontSize: 16, backgroundColor: 'var(--primary)', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer' }}
            >
              ➕ 新建会话开始问答
            </button>
          </div>
        </div>
      </div>
    )
  }
  
  // 如果没有激活的会话，显示欢迎界面
  if (!activeId) {
    return (
      <div className="chat-area">
        <div className="chat-header">
          <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
          <div className="chat-header-actions">
            <ThemeToggle theme={theme} onToggleTheme={() => onToggleTheme()} />
          </div>
          {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right', marginRight: 12 }}>
              <span title="知识库状态" style={{ cursor: 'help' }}>{stats.status === 'connected' ? '🟢' : '🔴'}</span>
              {' '}
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
            {' | '}
            <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
          </div>
        )}
      </div>
        <div className="message-list-empty">
          <div style={{ fontSize: 32, marginBottom: 20 }}>👋 欢迎使用</div>
          <div style={{ fontSize: 18, marginBottom: 10, color: 'var(--text-secondary)' }}>面向银行业监管制度与统计报表的可信RAG问答系统</div>
          <div style={{ fontSize: 16, marginBottom: 30, color: 'var(--text-secondary)' }}>专注于银行业监管制度与统计报表的智能问答服务</div>
          <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.8 }}>
            <p>📚 支持查询银行业监管制度文件</p>
            <p>📊 支持统计报表分析与解读</p>
            <p>🤖 基于可信RAG技术，提供准确可靠的回答</p>
          </div>
          <div style={{ marginTop: 40 }}>
            <button
              onClick={onNewConv}
              style={{ padding: '12px 32px', fontSize: 16, backgroundColor: 'var(--primary)', color: 'white', border: 'none', borderRadius: 8, cursor: 'pointer' }}
            >
              ➕ 新建会话开始问答
            </button>
          </div>
        </div>
      </div>
    )
  }

  const toggleThinking = (idx: number) => {
    setExpandedThinking(prev =>
      prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
    )
  }

  const toggleSources = (idx: number) => {
    setExpandedSources(prev =>
      prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
    )
  }

  const buildFileLink = (fileName: string, page: number = 0) => {
    const ext = fileName.toLowerCase().split('.').pop() || ''
    const subdirMap: Record<string, string> = {
      pdf: 'pdf', doc: 'word/doc', docx: 'word/docx',
      xls: 'excel', xlsx: 'excel',
    }
    const subdir = subdirMap[ext] || ''
    if (!subdir) return fileName
    let href = getFileUrl(fileName)
    if (page > 0) href += `#page=${page}`
    return href
  }

  const nodeNames: Record<string, string> = {
    planner: '规划器', rag_searcher: 'RAG检索',
    summarize: '总结生成', dispatcher: '调度器',
    supervisor: '监督器', '__start__': '初始化',
  }

  if (!activeId) {
    return (
      <div className="chat-area">
        <div className="chat-header">
          <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 10, alignItems: 'center' }}>

          </div>
          {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right' }}>
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
              {' | '}
              <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
            </div>
          )}
        </div>
        <div className="message-list-empty">
          <h2>👋 欢迎使用</h2>
          <p>点击左侧「➕ 新建会话」开始今天的问答</p>
        </div>
      </div>
    )
  }

  const lastResult = testResults[testResults.length - 1]
  const nextQidFromLast = (r: TestResult) => {
    const num = parseInt(r.qid.replace(/^Q/i, ''), 10)
    return isNaN(num) ? r.qid : `Q${String(num + 1).padStart(3, '0')}`
  }
  const currentQid = testRunning ? (lastResult ? nextQidFromLast(lastResult) : '') : ''
  const nextQid = testStopped ? (lastResult ? nextQidFromLast(lastResult) : '') : ''

  return (
    <div className="chat-area">
      <div className="chat-header">
        <span className="chat-header-title">🤖 面向银行业监管制度与统计报表的可信RAG问答系统</span>
        {stats && (
            <div style={{ fontSize: 15, color: 'var(--text-secondary)', whiteSpace: 'nowrap', textAlign: 'right', marginRight: 12 }}>
              <span title="知识库状态" style={{ cursor: 'help' }}>{stats.status === 'connected' ? '🟢' : '🔴'}</span>
              {' '}
              <span title="当前知识库数量" style={{ cursor: 'help' }}>📊 {stats.count.toLocaleString()} 条</span>
            {' | '}
            <span title="知识库最近一次更新时间" style={{ cursor: 'help' }}>🕐 {stats.update_time}</span>
          </div>
        )}

        <div className="chat-header-actions">
          <ThemeToggle theme={theme} onToggleTheme={() => onToggleTheme()} />
          <button onClick={onNewConv}>新会话</button>
          <button
            onClick={handleExportMarkdown}
            disabled={messages.length === 0}
            title="导出对话为 Markdown"
          >
            📥 导出 MD
          </button>
        </div>
      </div>

      {/* 文档引用提示 */}
      {selectedDocs.length > 0 && (
        <div className="doc-refs-bar">
          📎 已引用 {selectedDocs.length} 个文档
          <button
            onClick={onClearDocs}
            style={{ marginLeft: 8, padding: '2px 8px', fontSize: 12, border: '1px solid #e74c3c', borderRadius: 4, background: '#fff', color: '#e74c3c', cursor: 'pointer' }}
          >
            清空
          </button>
          {selectedDocs.map((d, i) => (
            <div key={d.id || `${d.scope || 'public'}_${d.name}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '2px 0' }}>
              <span>
                <strong>[{i + 1}]</strong> {d.name}
                <small style={{ marginLeft: 6, color: 'var(--text-secondary)' }}>
                  {d.scope === 'personal' ? '个人' : '公共'}
                </small>
              </span>
              <button
                onClick={() => onRemoveDoc(d)}
                style={{
                  background: 'none', border: 'none', color: '#e74c3c', cursor: 'pointer',
                  fontSize: 14, padding: '0 4px', lineHeight: 1,
                }}
                title="取消引用"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 快速测试面板 */}
      <TestPanel testRunning={testRunning} testProgress={testProgress} onRunTest={onRunTest} onStopTest={onStopTest} testStopped={testStopped} onContinueTest={onContinueTest} currentQid={testCurrentQid} nextQid={testNextQid} testHistory={testHistory} />

      {/* 测试结果展示 */}
      {(testResults.length > 0 || testRunning) && (
        <TestResultsBlock
          results={testResults}
          summary={testSummary}
          running={testRunning}
          progress={testProgress}
          datasetName={testDataset}
          onStopTest={onStopTest}
          testStopped={testStopped}
          onContinueTest={onContinueTest}
          currentQid={currentQid}
          nextQid={nextQid}
        />
      )}

      <div className="message-list" ref={listRef}>
        {messages.map((msg, idx) => (
          <div key={idx} className={`message ${msg.role}`}>
            <div className="message-bubble" style={{ position: 'relative' }}>
              {msg.role === 'user' ? (
                msg.content
              ) : (
                <ReactMarkdown>{msg.content}</ReactMarkdown>
              )}
              <button
                onClick={() => handleCopy(msg.content)}
                title="复制内容"
                style={{
                  position: 'absolute', top: 4, right: 4,
                  padding: '1px 6px', fontSize: 11,
                  border: '1px solid #ddd', borderRadius: 4,
                  background: '#fff', cursor: 'pointer',
                  opacity: 0.6,
                }}
                onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                onMouseLeave={e => (e.currentTarget.style.opacity = '0.6')}
              >
                📋
              </button>
            </div>

            {/* 思考过程 */}
            {msg.role === 'assistant' && msg.thinking && (
              <div className="thinking-panel">
                <div className="thinking-toggle" onClick={() => toggleThinking(idx)}>
                  {expandedThinking.includes(idx) ? '▼' : '▶'} 💭 思考过程
                </div>
                {expandedThinking.includes(idx) && (
                  <div className="thinking-content">{msg.thinking}</div>
                )}
              </div>
            )}

            {/* 来源证据 */}
            {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
              <div className="source-panel">
                <div className="source-toggle" onClick={() => toggleSources(idx)}>
                  {expandedSources.includes(idx) ? '▼' : '▶'} 📚 信息来源 ({msg.sources.length})
                </div>
                {expandedSources.includes(idx) && (
                  <div className="source-content">
                    {msg.sources.map((src, si) => (
                      <div key={si} className="source-item">
                        <div className="source-item-header">
                          <strong>{si + 1}.</strong>{' '}
                          <a href={buildFileLink(src.file_name, src.page)} target="_blank" rel="noopener noreferrer">
                            {src.file_name}
                          </a>
                          {src.score > 0 && (
                            <span className="source-item-score">⭐{src.score.toFixed(4)}</span>
                          )}
                        </div>
                        {src.section && <div className="source-item-section">{src.section}</div>}
                        {src.preview && <div className="source-item-preview">{src.preview}</div>}
                        {src.full_text && (
                          <div className="source-item-full">{src.full_text}</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* 计时 */}
            {msg.role === 'assistant' && msg.timing && (
              <div className="timing-info">
                ⏱️ <strong>总用时</strong>: {msg.timing.total} 秒
                {Object.entries(msg.timing.nodes).map(([name, duration]) => (
                  <span key={name} style={{ marginLeft: 12 }}>
                    └─ {nodeNames[name] || name}: {duration}s
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {/* 流式渲染 */}
        {streaming && (
          <div className="message assistant">
            <div className="message-bubble streaming-cursor">
              {streaming.answer ? (
                <ReactMarkdown>{streaming.answer}</ReactMarkdown>
              ) : (
                <span style={{ color: 'var(--text-secondary)' }}>思考中...</span>
              )}
            </div>
            {streaming.thinking && (
              <div className="thinking-panel">
                <div className="thinking-content">{streaming.thinking}</div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 当前模型显示 */}
      <div style={{ padding: '8px 16px', borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 14, color: 'var(--text-secondary)' }}>
          🤖 当前模型: 
          <span style={{ fontWeight: 'bold', color: 'var(--text)', marginLeft: 8, marginRight: 8 }}>
            {currentModel.split('【')[0]}
          </span>
          {currentModel.includes('已配置') && (
            <span style={{ color: '#4caf50', fontSize: 12 }}>
              【已配置api-key】
            </span>
          )}
          {currentModel.includes('未配置') && (
            <span style={{ color: '#ffc107', fontSize: 12 }}>
              【未配置api-key，前往系统设置进行配置】
            </span>
          )}
        </span>
      </div>

      {/* 输入区 */}
      <div className="input-area">
        <div className="input-row">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={e => { setInput(e.target.value); adjustHeight() }}
            onKeyDown={handleKeyDown}
            onInput={adjustHeight}
            placeholder="输入你的问题... (Enter 发送, Shift+Enter 换行)"
            rows={1}
            disabled={loading}
          />
          <button onClick={handleSend} disabled={loading || !input.trim()}>
            {loading ? '发送中...' : '发送'}
          </button>
        </div>
        <div className="input-hint">
          <span><kbd>Enter</kbd> 发送</span>
          <span><kbd>Shift+Enter</kbd> 换行</span>
          <span><kbd>Ctrl+N</kbd> 新建会话</span>
          <span><kbd>Ctrl+/</kbd> 快捷键</span>
          <span>{input.length} 字</span>
        </div>
      </div>

      {/* 快捷键弹窗 */}
      {showShortcuts && (
        <div className="shortcut-overlay" onClick={() => setShowShortcuts(false)}>
          <div className="shortcut-modal" onClick={e => e.stopPropagation()}>
            <h3>⌨️ 键盘快捷键</h3>
            <div className="shortcut-row">
              <span>发送消息</span>
              <span><kbd>Enter</kbd></span>
            </div>
            <div className="shortcut-row">
              <span>发送消息（不换行）</span>
              <span><kbd>Ctrl+Enter</kbd></span>
            </div>
            <div className="shortcut-row">
              <span>换行</span>
              <span><kbd>Shift+Enter</kbd></span>
            </div>
            <div className="shortcut-row">
              <span>新建会话</span>
              <span><kbd>Ctrl+N</kbd></span>
            </div>
            <div className="shortcut-row">
              <span>切换主题</span>
              <span><kbd>Ctrl+/</kbd> 打开此面板</span>
            </div>
            <button onClick={() => setShowShortcuts(false)}>关闭</button>
          </div>
        </div>
      )}
    </div>
  )
}

function TestResultsBlock({ results, summary, running, progress, datasetName, onStopTest, testStopped, onContinueTest, currentQid, nextQid }: {
  results: TestResult[]
  summary: TestSummary | null
  running: boolean
  progress: number
  datasetName: string
  onStopTest: () => void
  testStopped: boolean
  onContinueTest: () => void
  currentQid: string
  nextQid: string
}) {
  const [expanded, setExpanded] = useState(false)
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())

  const diffLabel = (d: string) => {
    const map: Record<string, string> = { easy: '🟢 简单', medium: '🟡 中等', hard: '🔴 困难' }
    return map[d] || d
  }

  const agentNames: Record<string, string> = {
    planner: '规划器', rag_searcher: 'RAG检索',
    summarize: '总结生成', dispatcher: '调度器',
    supervisor: '监督器', '__start__': '初始化',
  }

  const toggleRow = (idx: number) => {
    setExpandedRows(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const handleExportReport = () => {
    const lines: string[] = []
    lines.push(`# 批量测试报告 — ${datasetName}\n`)
    lines.push(`> 导出时间: ${new Date().toLocaleString()}\n`)
    if (summary) {
      lines.push(`## 📊 总览\n`)
      lines.push(`| 指标 | 数值 |`)
      lines.push(`|------|------|`)
      lines.push(`| 总题数 | ${summary.total} |`)
      lines.push(`| 正确 | ${summary.correct} |`)
      lines.push(`| 错误 | ${summary.wrong} |`)
      lines.push(`| 准确率 | ${summary.accuracy}% |`)
      lines.push(`| 平均用时 | ${summary.avg_time}s |`)
      lines.push(`| 总用时 | ${summary.total_time}s |`)
      lines.push('')
      if (summary.difficulty_stats) {
        lines.push(`## 📈 难度分布\n`)
        lines.push(`| 难度 | 总数 | 正确 | 准确率 |`)
        lines.push(`|------|------|------|--------|`)
        Object.entries(summary.difficulty_stats).forEach(([diff, stat]) => {
          lines.push(`| ${diffLabel(diff)} | ${stat.total} | ${stat.correct} | ${stat.total > 0 ? (stat.correct / stat.total * 100).toFixed(1) : '0'}% |`)
        })
        lines.push('')
      }
    }
    lines.push(`## 📋 详细结果\n`)
    results.forEach((r, i) => {
      const resultIcon = r.correct ? '✅' : '❌'
      lines.push(`### ${i + 1}. ${r.qid} ${resultIcon} [${diffLabel(r.difficulty)}] ⏱️ ${r.elapsed}s\n`)
      if (r.question) {
        lines.push(`**题目:** ${r.question}\n`)
      }
      if (r.options) {
        lines.push(`**选项:**\n`)
        Object.entries(r.options).forEach(([k, v]) => {
          lines.push(`- ${k}. ${v}`)
        })
        lines.push('')
      }
      lines.push(`- 模型答案: **${r.answer}** | 预期答案: **${r.expected}** | ${r.correct ? '✅ 正确' : '❌ 错误'}`)
      if (r.timing) {
        const agents = Object.entries(r.timing).filter(([k]) => k !== 'LangGraph')
        if (agents.length > 0) {
          lines.push(`- 耗时: ${agents.map(([k, v]) => `${k} ${v}s`).join(' · ')}`)
        }
      }
      lines.push('')
    })
    lines.push('')
    if (summary && summary.wrong_details && summary.wrong_details.length > 0) {
      lines.push(`## ❌ 错题详情\n`)
      summary.wrong_details.forEach((wd, i) => {
        lines.push(`### ${i + 1}. ${wd.id} [${diffLabel(wd.difficulty)}]\n`)
        lines.push(`**题目:** ${wd.question}\n`)
        lines.push(`- 预期答案: ${wd.expected}`)
        lines.push(`- 实际答案: ${wd.got}`)
        lines.push(`- 来源: ${wd.source}\n`)
      })
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `测试报告_${datasetName.replace(/\.[^.]+$/, '')}_${new Date().toISOString().slice(0, 10)}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const correctCount = results.filter(r => r.correct).length
  const wrongCount = results.filter(r => !r.correct).length

  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--card-bg)' }}>
      {/* 摘要条 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 17 }}>🧪 批量测试 {datasetName && <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>— {datasetName}</span>}</span>
        {running && (
          <>
            <span style={{ fontSize: 15, color: 'var(--text-secondary)' }}>运行中 {progress}%</span>
            <div style={{ flex: 1, height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden', maxWidth: 200 }}>
              <div style={{ height: '100%', width: `${progress}%`, background: '#4caf50', transition: 'width 0.3s' }} />
            </div>
            <button
              onClick={onStopTest}
              title={currentQid ? `当前正在运行 ${currentQid}，此题目运行完后停止` : '正在运行第一道题目，此题目运行完后停止'}
              style={{ padding: '3px 10px', fontSize: 13, background: '#e74c3c', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
            >
              ⏹️ 停止
            </button>
          </>
        )}
        {summary && (
          <>
            <span style={{ fontSize: 15 }}>✅ {summary.correct} <span style={{ color: 'var(--text-secondary)' }}>正确</span></span>
            <span style={{ fontSize: 15 }}>❌ {summary.wrong} <span style={{ color: 'var(--text-secondary)' }}>错误</span></span>
            <span style={{ fontSize: 15, fontWeight: 700, color: summary.accuracy >= 80 ? '#4caf50' : '#e74c3c' }}>🎯 {summary.accuracy}%</span>
            <span style={{ fontSize: 15, color: 'var(--text-secondary)' }}>⏱️ {summary.avg_time}s/题</span>
          </>
        )}
        {testStopped && (
          <button
            onClick={onContinueTest}
            title={nextQid ? `点击继续从 ${nextQid} 开始` : ''}
            style={{ padding: '3px 10px', fontSize: 13, background: '#4caf50', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
          >
            ▶️ 继续
          </button>
        )}
        {!summary && !running && !testStopped && (
          <span style={{ fontSize: 15, color: 'var(--text-secondary)' }}>{results.length} 题已完成</span>
        )}
        <button
          onClick={() => setExpanded(!expanded)}
          style={{ marginLeft: 'auto', padding: '3px 10px', fontSize: 14, border: '1px solid var(--border)', borderRadius: 4, background: 'var(--card-bg)', cursor: 'pointer' }}
        >
          {expanded ? '收起详情 ▲' : '展开详情 ▼'}
        </button>
        {results.length > 0 && (
          <button
            onClick={handleExportReport}
            title="导出测试报告为 Markdown"
            style={{ padding: '3px 10px', fontSize: 14, border: '1px solid #4caf50', borderRadius: 4, background: 'var(--card-bg)', color: '#4caf50', cursor: 'pointer' }}
          >
            📥 导出报告
          </button>
        )}
      </div>

      {/* 展开详情 */}
      {expanded && (
        <div style={{ marginTop: 10, maxHeight: 300, overflowY: 'auto', fontSize: 14 }}>
          {/* 逐题结果 */}
          {results.map((r, i) => (
            <div key={i}>
              <div
                onClick={() => toggleRow(i)}
                style={{ padding: '3px 6px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6, background: r.correct ? 'var(--card-bg)' : '#fff5f5', cursor: 'pointer' }}
              >
                <span style={{ fontSize: 11, color: 'var(--text-secondary)', minWidth: 16 }}>{expandedRows.has(i) ? '▼' : '▶'}</span>
                <span>{r.correct ? '✅' : '❌'}</span>
                <span style={{ fontWeight: 600, minWidth: 40 }}>{r.qid}</span>
                <span style={{ color: r.correct ? '#4caf50' : '#e74c3c', fontWeight: 600 }}>{r.answer}</span>
                {!r.correct && <span style={{ color: 'var(--text-secondary)' }}>期望 {r.expected}</span>}
                <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>[{diffLabel(r.difficulty)}]</span>
                <span style={{ marginLeft: 'auto', color: 'var(--text)', fontSize: 11, fontWeight: 700 }}>⏱️ {r.elapsed} 秒</span>
                {r.error && <span style={{ color: '#e74c3c', fontSize: 11 }}>⚠️ {r.error}</span>}
              </div>
              {expandedRows.has(i) && (
                <div style={{ padding: '6px 10px', background: 'var(--thinking-bg)', borderBottom: '1px solid var(--border)', fontSize: 13, lineHeight: 1.6 }}>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>📋 题干</div>
                  <div style={{ color: 'var(--text)', marginBottom: 6 }}>{r.question || '(无题干)'}</div>
                  {r.options && Object.keys(r.options).length > 0 && (
                    <>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>📝 选项</div>
                      <div style={{ color: 'var(--text)', marginBottom: 6 }}>
                        {Object.entries(r.options).map(([k, v]) => (
                          <div key={k}>
                            <span style={{ fontWeight: 600 }}>{k}.</span> {v}
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                  <div style={{ display: 'flex', gap: 16 }}>
                    <span>🎯 预期答案: <span style={{ color: '#4caf50', fontWeight: 600 }}>{r.expected}</span></span>
                    <span>🤖 预测答案: <span style={{ color: r.correct ? '#4caf50' : '#e74c3c', fontWeight: 600 }}>{r.answer}</span></span>
                  </div>
                  <div style={{ marginTop: 4, padding: '4px 8px', background: 'var(--thinking-bg)', borderRadius: 4, fontSize: 12 }}>
                    <span style={{ fontWeight: 700 }}>⏱️ 总用时: {r.elapsed} 秒</span>
                    {r.timing && Object.keys(r.timing).filter(k => k !== 'LangGraph').length > 0 && (
                      Object.entries(r.timing).filter(([k]) => k !== 'LangGraph').map(([k, v]) => (
                        <span key={k} style={{ marginLeft: 12 }}>
                          └─ {agentNames[k] || k}: {v}s
                        </span>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}

          {/* 错题详情 */}
          {summary && summary.wrong_details.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>📝 错题详情</div>
              {summary.wrong_details.map((w, i) => (
                <div key={i} style={{ padding: '6px 8px', marginBottom: 4, background: 'var(--card-bg)', borderRadius: 4, border: '1px solid var(--border)', fontSize: 14 }}>
                  <div style={{ fontWeight: 600 }}>{w.id} [{diffLabel(w.difficulty)}] 📄 {w.source}</div>
                  <div style={{ color: 'var(--text-secondary)', marginTop: 2 }}>{w.question}...</div>
                  <div style={{ marginTop: 2 }}>
                    期望: <span style={{ color: '#4caf50', fontWeight: 600 }}>{w.expected}</span>
                    {' | '}
                    实际: <span style={{ color: '#e74c3c', fontWeight: 600 }}>{w.got}</span>
                  </div>
                  <details style={{ marginTop: 2 }}>
                    <summary style={{ cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 11 }}>查看回复</summary>
                    <div style={{ whiteSpace: 'pre-wrap', color: 'var(--text-secondary)', fontSize: 11, maxHeight: 100, overflowY: 'auto', background: 'var(--thinking-bg)', padding: 4, borderRadius: 2, marginTop: 2 }}>{w.reply_preview}</div>
                  </details>
                </div>
              ))}
            </div>
          )}

          {/* 难度统计 */}
          {summary && summary.difficulty_stats && (
            <div style={{ marginTop: 10, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              {Object.entries(summary.difficulty_stats).map(([k, v]) => {
                const acc = v.total > 0 ? Math.round(v.correct / v.total * 100) : 0
                return (
                  <div key={k} style={{ fontSize: 14, padding: '4px 8px', background: '#fff', borderRadius: 4, border: '1px solid #eee' }}>
                    {diffLabel(k)}: {v.correct}/{v.total} ({acc}%)
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}