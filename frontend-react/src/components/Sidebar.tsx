import { useState, useRef, useEffect } from 'react'
import SettingsPanel from './SettingsPanel'
import type { AuthUser, Conversation, DocInfo } from '../types'
import {
  deletePersonalKnowledgeDoc,
  fetchPersonalKnowledgeDocs,
  fetchPublicKnowledgeDocs,
  uploadPersonalKnowledgeFiles,
  uploadPublicKnowledgeFiles,
} from '../api/client'
import type { KnowledgeScope } from '../api/client'
import { groupConversations } from '../store/sessionStore'

interface Props {
  convs: Conversation[]
  activeId: string | null
  selectedDocs: DocInfo[]
  onSelectConv: (id: string) => void
  onNewConv: () => void
  onDeleteConv: (id: string) => void
  onRenameConv: (id: string, title: string) => void
  onTogglePin: (id: string) => void
  onSelectDocs: (docs: DocInfo[]) => void
  onRefresh: () => void
  token: string
  currentUser: AuthUser
  activePage: 'chat' | 'admin'
  onChangePage: (page: 'chat' | 'admin') => void
  onLogout: () => void
}

interface MenuState {
  convId: string
  x: number
  y: number
}

interface DocListItem {
  id?: string
  name: string
  type?: DocInfo['type']
  path?: string
  scope?: KnowledgeScope
  size?: number
  modified?: number
  chunk_count?: number
  status?: string
}

export default function Sidebar({ token,
  convs, activeId, selectedDocs,
  onSelectConv, onNewConv, onDeleteConv, onRenameConv, onTogglePin,
  onSelectDocs, onRefresh,
  currentUser, activePage, onChangePage, onLogout,
}: Props) {
  const [menu, setMenu] = useState<MenuState | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const menuRef = useRef<HTMLDivElement>(null)

  // 文档列表
  const [publicDocList, setPublicDocList] = useState<Record<string, DocListItem[]>>({ pdf: [], word: [], excel: [] })
  const [personalDocList, setPersonalDocList] = useState<Record<string, DocListItem[]>>({ pdf: [], word: [], excel: [] })
  const [docExpanded, setDocExpanded] = useState<Record<string, boolean>>({ pdf: false, word: false, excel: false })
  const [docLoading, setDocLoading] = useState(false)
  const [showPublicDocPanel, setShowPublicDocPanel] = useState(false)
  const [showPersonalDocPanel, setShowPersonalDocPanel] = useState(false)
  const [showUploadPanel, setShowUploadPanel] = useState(false)

  // 知识库导入
  const [uploadFiles, setUploadFiles] = useState<{name: string, file: File}[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const [uploadScope, setUploadScope] = useState<KnowledgeScope>('personal')
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 关闭弹出菜单
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenu(null)
      }
    }
    if (menu) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menu])

  // 加载文档列表
  const fetchDocs = async () => {
    setDocLoading(true)
    const [publicResult, personalResult] = await Promise.allSettled([
      fetchPublicKnowledgeDocs(token),
      fetchPersonalKnowledgeDocs(token),
    ])
    if (publicResult.status === 'fulfilled') setPublicDocList(publicResult.value)
    if (personalResult.status === 'fulfilled') setPersonalDocList(personalResult.value)
    setDocLoading(false)
  }

  useEffect(() => { fetchDocs() }, [token])

  // 切换文档选中
  const toggleDoc = (doc: DocListItem, type: string) => {
    const scope = doc.scope || 'public'
    const isSameDoc = (item: DocInfo) => (
      (doc.id && item.id === doc.id)
      || (!doc.id && item.name === doc.name && (item.scope || 'public') === scope)
    )
    const exists = selectedDocs.find(isSameDoc)
    if (exists) {
      onSelectDocs(selectedDocs.filter(d => !isSameDoc(d)))
    } else {
      onSelectDocs([...selectedDocs, {
        id: doc.id,
        name: doc.name,
        type: type as DocInfo['type'],
        path: doc.path || (doc.id ? `personal/${doc.id}` : `${type}/${doc.name}`),
        scope,
        chunk_count: doc.chunk_count,
        status: doc.status,
      }])
    }
  }

  // 上传文件导入知识库
  const handleUpload = async () => {
    if (uploadFiles.length === 0) return
    setUploading(true)
    setUploadMsg('上传中...')
    try {
      const files = uploadFiles.map(f => f.file)
      const data = uploadScope === 'public'
        ? await uploadPublicKnowledgeFiles(token, files)
        : await uploadPersonalKnowledgeFiles(token, files)
      if (data.status === 'success') {
        const parts: string[] = []
        if (data.details?.word) parts.push(`Word: ${data.details.word}`)
        if (data.details?.pdf) parts.push(`PDF: ${data.details.pdf}`)
        if (data.details?.excel) parts.push(`Excel: ${data.details.excel}`)
        const targetName = uploadScope === 'public' ? '公共知识库' : '我的知识库'
        setUploadMsg(`✅ 已导入${targetName}：共 ${data.total_chunks} 个知识单元${parts.length ? ` (${parts.join(', ')})` : ''}`)
        setUploadFiles([])
        fetchDocs()
      } else {
        setUploadMsg('❌ 导入失败')
      }
    } catch (e: any) {
      setUploadMsg(`❌ 错误: ${e.message}`)
    }
    setUploading(false)
  }

  const handleDeleteDoc = async (doc: DocListItem) => {
    if (!doc.id) return
    if (!window.confirm(`确认从我的知识库删除「${doc.name}」吗？`)) return
    setUploadMsg('删除中...')
    try {
      await deletePersonalKnowledgeDoc(token, doc.id)
      onSelectDocs(selectedDocs.filter(d => d.id !== doc.id && d.name !== doc.name))
      setUploadMsg('✅ 文件已从我的知识库删除')
      fetchDocs()
    } catch (e: any) {
      setUploadMsg(`❌ 删除失败: ${e.message}`)
    }
  }

  const groups = groupConversations(convs)

  const renderDocumentGroups = (
    list: Record<string, DocListItem[]>,
    scope: KnowledgeScope,
  ) => (['pdf', 'word', 'excel'] as const).map(type => {
    const label = { pdf: '📄 PDF', word: '📝 Word', excel: '📊 Excel' }[type]
    const files = list[type] || []
    const expandedKey = `${scope}_${type}`
    return (
      <div key={expandedKey} className="doc-section">
        <div
          className="doc-section-title"
          onClick={() => setDocExpanded(prev => ({ ...prev, [expandedKey]: !prev[expandedKey] }))}
        >
          {docExpanded[expandedKey] ? '▼' : '▶'} {label} ({files.length})
        </div>
        {docExpanded[expandedKey] && files.map(file => {
          const normalized = { ...file, scope }
          const selected = selectedDocs.some(item => (
            (file.id && item.id === file.id)
            || (!file.id && item.name === file.name && (item.scope || 'public') === scope)
          ))
          return (
            <div
              key={file.id || `${scope}_${type}_${file.name}`}
              className={`doc-item ${selected ? 'selected' : ''}`}
              onClick={() => toggleDoc(normalized, type)}
              title={file.name}
            >
              <input
                type="checkbox"
                checked={selected}
                onChange={() => toggleDoc(normalized, type)}
                onClick={event => event.stopPropagation()}
              />
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12, flex: 1 }}>
                {file.name}
                {typeof file.chunk_count === 'number' && (
                  <span style={{ color: 'var(--text-secondary)' }}> · {file.chunk_count} 块</span>
                )}
              </span>
              {scope === 'personal' && file.id && (
                <button
                  onClick={event => {
                    event.stopPropagation()
                    handleDeleteDoc(normalized)
                  }}
                  style={{ background: 'transparent', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: 12, padding: '0 2px' }}
                  title="删除个人文件"
                >
                  删除
                </button>
              )}
            </div>
          )
        })}
      </div>
    )
  })

  const handleNewConversation = () => {
    onChangePage('chat')
    onNewConv()
  }

  const renderGroup = (title: string, items: Conversation[]) => {
    if (items.length === 0) return null
    return (
      <div className="session-group">
        <div className="session-group-title">{title}</div>
        {items.map(c => (
          <div
            key={c.id}
            className={`session-item ${c.id === activeId ? 'active' : ''}`}
            onClick={() => {
              onChangePage('chat')
              onSelectConv(c.id)
            }}
          >
            {renaming === c.id ? (
              <input
                value={renameValue}
                onChange={e => setRenameValue(e.target.value)}
                onBlur={() => {
                  onRenameConv(c.id, renameValue || c.title)
                  setRenaming(null)
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    onRenameConv(c.id, renameValue || c.title)
                    setRenaming(null)
                  }
                }}
                autoFocus
                onClick={e => e.stopPropagation()}
                style={{ flex: 1, padding: '4px 6px', border: '1px solid var(--border)', borderRadius: 4, fontSize: 13 }}
              />
            ) : (
              <span className="session-item-title">{c.title}</span>
            )}
            <button
              className="session-item-menu"
              onClick={e => {
                e.stopPropagation()
                const rect = (e.target as HTMLElement).getBoundingClientRect()
                setMenu({ convId: c.id, x: rect.left, y: rect.bottom })
              }}
            >
              ⋯
            </button>
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <button onClick={handleNewConversation}>➕ 新建会话</button>
        <button onClick={() => setSettingsOpen(true)}>⚙️ 系统设置</button>
      </div>

      <div className="sidebar-account">
        <div>
          <div className="sidebar-account-name">{currentUser.full_name || currentUser.username}</div>
          <div className="sidebar-account-meta">
            {currentUser.role === 'admin' ? '管理员' : '普通用户'}
            {currentUser.department ? ` · ${currentUser.department}` : ''}
          </div>
        </div>
        <button onClick={onLogout}>退出</button>
      </div>

      <div className="sidebar-nav">
        <button
          className={activePage === 'chat' ? 'active' : ''}
          onClick={() => onChangePage('chat')}
        >
          问答工作台
        </button>
        {currentUser.role === 'admin' && (
          <button
            className={activePage === 'admin' ? 'active' : ''}
            onClick={() => onChangePage('admin')}
          >
            用户管理
          </button>
        )}
      </div>

      {/* 可滚动区域：会话列表 + 文档引用 + 知识库导入 */}
      <div className="sidebar-body" style={{ display: 'flex', flexDirection: 'column', overflow: 'visible' }}>
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {convs.length === 0 ? (
          <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 14 }}>
            点击「新建会话」开始今天的问答
          </div>
        ) : (
          <>
            {renderGroup('📌 置顶', groups.pinned)}
            {renderGroup('📅 今天', groups.today)}
            {renderGroup('📆 7 天内', groups.week)}
            {renderGroup('📦 更早', groups.older)}
          </>
        )}

        {/* 公共知识库文档快速引用（折叠） */}
        <div style={{ borderTop: '1px solid var(--border)', padding: '8px' }}>
          <div
            onClick={() => setShowPublicDocPanel(!showPublicDocPanel)}
            style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', padding: '4px 4px', cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 4 }}
          >
            {showPublicDocPanel ? '▼' : '▶'} 🌐 公共知识库
          </div>
          {showPublicDocPanel && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 8, marginTop: 8, background: 'var(--thinking-bg)' }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: 8 }}>
                来自 data/pdf、data/word、data/excel，所有登录用户共享。
              </div>
              {renderDocumentGroups(publicDocList, 'public')}
              <button
                onClick={fetchDocs}
                style={{
                  width: '100%', padding: '4px', marginTop: 4, border: '1px solid var(--border)',
                  borderRadius: 4, background: 'var(--card-bg)', cursor: 'pointer', fontSize: 12,
                }}
              >
                {docLoading ? '加载中...' : '🔄 刷新公共知识库'}
              </button>
            </div>
          )}
        </div>

        {/* 个人知识库文档快速引用（折叠） */}
        <div style={{ borderTop: '1px solid var(--border)', padding: '8px' }}>
          <div
            onClick={() => setShowPersonalDocPanel(!showPersonalDocPanel)}
            style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', padding: '4px 4px', cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 4 }}
          >
            {showPersonalDocPanel ? '▼' : '▶'} 🔒 我的知识库
          </div>
          {showPersonalDocPanel && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 8, marginTop: 8, background: 'var(--thinking-bg)' }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: 8 }}>
                仅当前账号可见；同名上传会替换自己的旧版本。
              </div>
              {renderDocumentGroups(personalDocList, 'personal')}
              <button
                onClick={fetchDocs}
                style={{
                  width: '100%', padding: '4px', marginTop: 4, border: '1px solid var(--border)',
                  borderRadius: 4, background: 'var(--card-bg)', cursor: 'pointer', fontSize: 12,
                }}
              >
                {docLoading ? '加载中...' : '🔄 刷新我的知识库'}
              </button>
            </div>
          )}
        </div>

        {/* 公共/个人知识库导入（折叠） */}
        <div className="kb-section" style={{ borderTop: '1px solid var(--border)' }}>
          <div
            onClick={() => setShowUploadPanel(!showUploadPanel)}
            style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', padding: '4px 4px', cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 4 }}
          >
            {showUploadPanel ? '▼' : '▶'} 📚 导入知识库
          </div>
          {showUploadPanel && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 8, marginTop: 8, background: 'var(--thinking-bg)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 6 }}>
                <button
                  type="button"
                  onClick={() => setUploadScope('personal')}
                  style={{
                    padding: '6px 4px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                    border: uploadScope === 'personal' ? '1px solid var(--primary)' : '1px solid var(--border)',
                    background: uploadScope === 'personal' ? 'var(--user-bubble)' : 'var(--card-bg)',
                    color: 'var(--text)',
                  }}
                >
                  🔒 我的知识库
                </button>
                <button
                  type="button"
                  onClick={() => setUploadScope('public')}
                  style={{
                    padding: '6px 4px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                    border: uploadScope === 'public' ? '1px solid var(--primary)' : '1px solid var(--border)',
                    background: uploadScope === 'public' ? 'var(--user-bubble)' : 'var(--card-bg)',
                    color: 'var(--text)',
                  }}
                >
                  🌐 公共知识库
                </button>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: 6 }}>
                {uploadScope === 'public'
                  ? '所有登录用户都能使用；同名文件会替换公共版本。'
                  : '文件仅归当前账号所有，删除账号时会一起清理。'}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.doc,.xlsx,.xls"
                style={{ display: 'none' }}
                onChange={e => {
                  if (e.target.files && e.target.files.length > 0) {
                    const files = Array.from(e.target.files!).map(f => ({ name: f.name, file: f }))
                    setUploadFiles(prev => [...prev, ...files])
                    if (fileInputRef.current) fileInputRef.current.value = ''
                  }
                }}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                style={{ width: '100%', padding: '6px', border: '2px dashed var(--border)', borderRadius: 6, background: 'var(--thinking-bg)', color: 'var(--text)', cursor: 'pointer', fontSize: 13, marginBottom: 6, marginTop: 6 }}
              >
                📂 选择文件 ({uploadFiles.length})
              </button>
              {uploadFiles.length > 0 && (
                <div style={{ marginTop: 6, maxHeight: 150, overflowY: 'auto' }}>
                  {uploadFiles.map((f, i) => (
                    <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'center', padding: '3px 0', fontSize: 12, borderBottom: '1px solid var(--border)' }}>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={f.name}>
                        {i + 1}. {f.name}
                      </span>
                      <button
                        onClick={() => setUploadFiles(prev => prev.filter((_, j) => j !== i))}
                        style={{ background: 'none', border: 'none', color: '#e74c3c', cursor: 'pointer', fontSize: 14, padding: '0 4px', flexShrink: 0 }}
                        title="移除"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <button
                onClick={handleUpload}
                disabled={uploading || uploadFiles.length === 0}
                style={{ marginTop: 8 }}
              >
                {uploading
                  ? '导入中...'
                  : `🚀 导入${uploadScope === 'public' ? '公共知识库' : '我的知识库'}`}
              </button>
              {uploadMsg && (
                <div className="kb-progress" style={{ marginTop: 8 }}>
                  {uploadMsg}
                  {uploading && <div className="kb-progress-bar"><div className="kb-progress-bar-fill" style={{ width: '60%' }} /></div>}
                </div>
              )}
            </div>
          )}
        </div>
        </div>
      </div>

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} token={token} />

      {/* 弹出菜单 */}
      {menu && (
        <div
          ref={menuRef}
          className="popup-menu"
          style={{ position: 'fixed', left: menu.x, top: menu.y }}
        >
          <div
            className="popup-menu-item"
            onClick={() => {
              const conv = convs.find(c => c.id === menu.convId)
              setRenaming(menu.convId)
              setRenameValue(conv?.title || '')
              setMenu(null)
            }}
          >
            ✏️ 重命名
          </div>
          <div
            className="popup-menu-item"
            onClick={() => {
              onTogglePin(menu.convId)
              setMenu(null)
            }}
          >
            📌 {convs.find(c => c.id === menu.convId)?.pinned ? '取消置顶' : '置顶'}
          </div>
          <div className="popup-menu-divider" />
          <div
            className="popup-menu-item danger"
            onClick={() => {
              onDeleteConv(menu.convId)
              setMenu(null)
            }}
          >
            🗑️ 删除
          </div>
        </div>
      )}
    </div>
  )
}
