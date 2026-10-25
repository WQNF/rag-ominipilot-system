
import { useState, useCallback, useEffect } from 'react'
import Login from './components/Login'
import ChatView from './components/ChatView'
import Sidebar from './components/Sidebar'
import AdminUsers from './components/AdminUsers'
import type { AuthUser, Conversation, DocInfo, LoginResponse } from './types'
import { loadConversations, createConversation, deleteConversation, updateConversation, togglePin } from './store/sessionStore'

const BASE = ''

interface TestResult { index: number; total: number; qid: string; correct: boolean; answer: string; expected: string; elapsed: number; difficulty: string; error?: string; question?: string; options?: Record<string, string>; timing?: Record<string, number> }
interface TestSummary { total: number; correct: number; wrong: number; errors: number; accuracy: number; avg_time: number; total_time: number; wrong_details: { id: string; question: string; expected: string; got: string; difficulty: string; source: string; reply_preview: string }[]; difficulty_stats: Record<string, { total: number; correct: number }> }

export default function App() {
  const [token, setToken] = useState<string | null>(null)
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null)
  const [activePage, setActivePage] = useState<'chat' | 'admin'>('chat')
  const [convs, setConvs] = useState<Conversation[]>(loadConversations)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [selectedDocs, setSelectedDocs] = useState<DocInfo[]>([])
  const [testResults, setTestResults] = useState<TestResult[]>([])
  const [testSummary, setTestSummary] = useState<TestSummary | null>(null)
  const [testRunning, setTestRunning] = useState(false)
  const [testProgress, setTestProgress] = useState(0)
  const [testDataset, setTestDataset] = useState('')
  const [testStopped, setTestStopped] = useState(false)
  const [testCurrentQid, setTestCurrentQid] = useState('')
  const [testNextQid, setTestNextQid] = useState('')
  const [testHistory, setTestHistory] = useState<{
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
  }[]>(() => {
    const saved = localStorage.getItem('test_history')
    return saved ? JSON.parse(saved) : []
  })

  // 清理已完成或停止的测试状态
  useEffect(() => {
    setTestHistory(prev => {
      return prev.map(item => {
        // 如果测试状态是running但没有在当前运行，标记为stopped
        if (item.status === 'running' && (!testRunning || item.dataset !== testDataset)) {
          return {
            ...item,
            status: 'stopped' as const,
            endTime: item.endTime || new Date().toISOString()
          }
        }
        return item
      })
    })
  }, [testRunning, testDataset])
  const [theme, setTheme] = useState<'light' | 'dark'>(
    () => (localStorage.getItem('theme') as 'light' | 'dark') || 'light'
  )

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  // 保存测试历史到localStorage
  useEffect(() => {
    localStorage.setItem('test_history', JSON.stringify(testHistory))
  }, [testHistory])

  useEffect(() => {
    if (currentUser?.role !== 'admin' && activePage === 'admin') {
      setActivePage('chat')
    }
  }, [activePage, currentUser])

  // 监听删除测试记录事件
  useEffect(() => {
    const handleDelete = (e: CustomEvent) => {
      const testId = e.detail
      setTestHistory(prev => prev.filter(item => item.id !== testId))
    }
    window.addEventListener('delete-test-history', handleDelete as EventListener)
    return () => window.removeEventListener('delete-test-history', handleDelete as EventListener)
  }, [])

  // 保存测试历史到localStorage
  useEffect(() => {
    localStorage.setItem('test_history', JSON.stringify(testHistory))
  }, [testHistory])
  
  // 页面加载时检查是否已有会话
  useEffect(() => {
    if (token) {
      const existingConvs = loadConversations()
      if (existingConvs.length > 0) {
        setActiveId(existingConvs[0].id)
      } else {
        setActiveId(null)
      }
    }
  }, [token])
  
  // 页面加载时检查是否已有会话
  useEffect(() => {
    if (token) {
      const existingConvs = loadConversations()
      if (existingConvs.length > 0) {
        setActiveId(existingConvs[0].id)
      } else {
        setActiveId(null)
      }
    }
  }, [token])

  const toggleTheme = (newTheme?: 'light' | 'dark' | React.SyntheticEvent) => {
    setTheme(t => {
      // 检查参数是否为事件对象，如果是则忽略
      if (typeof newTheme === 'object' && newTheme !== null && '_reactName' in newTheme) {
        console.warn('Received event object instead of theme string, toggling theme normally')
        const targetTheme = t === 'light' ? 'dark' : 'light'
        console.log('Theme changing from', t, 'to', targetTheme)
        return targetTheme
      }
      const targetTheme = (newTheme as 'light' | 'dark') || (t === 'light' ? 'dark' : 'light')
      console.log('Theme changing from', t, 'to', targetTheme)
      return targetTheme
    })
  }

  const refreshConvs = useCallback(() => {
    setConvs([...loadConversations()])
  }, [])

  const handleLogin = (session: LoginResponse) => {
    setToken(session.access_token)
    setCurrentUser({
      username: session.username,
      role: session.role,
      department: session.department,
      full_name: session.full_name,
    })
    setActivePage('chat')
    refreshConvs()
    // 检查是否已有会话，如果有则激活第一个会话
    const existingConvs = loadConversations()
    if (existingConvs.length > 0) {
      setActiveId(existingConvs[0].id)
    } else {
      setActiveId(null)
    }
  }

  const handleLogout = () => {
    setToken(null)
    setCurrentUser(null)
    setActivePage('chat')
    setSelectedDocs([])
  }

  const handleNewConv = () => {
    const c = createConversation()
    setActiveId(c.id)
    refreshConvs()
  }

  const handleSelectConv = (id: string) => {
    setActiveId(id)
  }

  const handleDeleteConv = (id: string) => {
    deleteConversation(id)
    if (activeId === id) {
      const remaining = loadConversations()
      setActiveId(remaining.length > 0 ? remaining[0].id : null)
    }
    refreshConvs()
  }

  const handleRenameConv = (id: string, title: string) => {
    updateConversation(id, { title })
    refreshConvs()
  }

  const handleTogglePin = (id: string) => {
    togglePin(id)
    refreshConvs()
  }

  const handleRemoveDoc = (doc: DocInfo) => {
    setSelectedDocs(prev => prev.filter(item => {
      if (doc.id) return item.id !== doc.id
      return !(item.name === doc.name && (item.scope || 'public') === (doc.scope || 'public'))
    }))
  }

  const handleClearDocs = () => {
    setSelectedDocs([])
  }

  const runTest = async (dataset: string, startQid?: string, testId?: string) => {
    setTestRunning(true)
    setTestResults([])
    setTestSummary(null)
    setTestProgress(0)
    setTestDataset(dataset)
    setTestStopped(false)
    setTestCurrentQid('')
    setTestNextQid('')
    
    // 修改会话名称为当前时间+测试集名称
    if (activeId) {
      const now = new Date()
      const timeStr = now.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      }).replace(/\//g, '-').replace(/:/g, '.')
      const testName = `[测试] ${timeStr} - ${dataset.replace('.py', '')}`
      updateConversation(activeId, { title: testName })
      refreshConvs() // 刷新会话列表
    }
    
    // 如果有testId，更新现有测试记录状态为running
    if (testId) {
      setTestHistory(prev => {
        return prev.map(item => {
          if (item.id === testId) {
            return { ...item, status: 'running' as const }
          }
          return item
        })
      })
    } else {
      // 没有testId，创建新的测试记录
      const newTest: {
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
      } = {
        id: Date.now().toString(),
        dataset: dataset,
        startTime: new Date().toISOString(),
        status: 'running',
        progress: 0,
        total: 0,
        correct: 0,
        wrong: 0,
        errors: 0
      }
      setTestHistory(prev => [...prev, newTest])
    }
    
    // 修改会话名称为当前时间+测试集名称
    if (activeId) {
      const now = new Date()
      const timeStr = now.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
      }).replace(/\//g, '-').replace(/:/g, '.')
      const testName = `[测试] ${timeStr} - ${dataset.replace('.py', '')}`
      updateConversation(activeId, { title: testName })
      refreshConvs() // 刷新会话列表
    }
    try {
      const resp = await fetch(`${BASE}/test/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset, start_qid: startQid }),
      })
      const reader = resp.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'result') {
                setTestResults(prev => [...prev, data])
                setTestProgress(Math.round(data.index / data.total * 100))
                setTestCurrentQid(data.qid)
                setTestNextQid(data.next_qid || '')
                // 更新当前测试的进度
                setTestHistory(prev => {
                  // 找到当前运行的测试记录（status为running的记录）
                  const runningIdx = prev.findIndex(item => item.status === 'running')
                  if (runningIdx >= 0) {
                    const updated = [...prev]
                    updated[runningIdx] = {
                      ...updated[runningIdx],
                      progress: Math.round(data.index / data.total * 100),
                      total: data.total,
                      correct: updated[runningIdx].correct + (data.correct ? 1 : 0),
                      wrong: updated[runningIdx].wrong + (data.correct ? 0 : 1),
                      errors: updated[runningIdx].errors + (data.error ? 1 : 0),
                      currentQid: data.qid,
                      nextQid: data.next_qid || ''
                    }
                    return updated
                  }
                  return prev
                })
              } else if (data.type === 'done') {
                setTestSummary(data)
                setTestProgress(100)
                setTestCurrentQid('')
                setTestNextQid('')
                // 标记测试完成
                setTestHistory(prev => {
                  const runningIdx = prev.findIndex(item => item.status === 'running')
                  if (runningIdx >= 0) {
                    const updated = [...prev]
                    updated[runningIdx] = {
                      ...updated[runningIdx],
                      status: 'completed',
                      endTime: new Date().toISOString(),
                      progress: 100,
                      total: data.total,
                      correct: data.correct,
                      wrong: data.wrong,
                      errors: data.errors,
                      currentQid: undefined,
                      nextQid: undefined
                    }
                    return updated
                  }
                  return prev
                })
              } else if (data.type === 'stopped') {
                setTestRunning(false)
                setTestStopped(true)
                // 标记测试停止
                setTestHistory(prev => {
                  const runningIdx = prev.findIndex(item => item.status === 'running')
                  if (runningIdx >= 0) {
                    const updated = [...prev]
                    updated[runningIdx] = {
                      ...updated[runningIdx],
                      status: 'stopped',
                      endTime: new Date().toISOString(),
                      currentQid: testCurrentQid,
                      nextQid: testNextQid
                    }
                    return updated
                  }
                  return prev
                })
                // 标记测试停止
                setTestHistory(prev => {
                  const lastIdx = prev.length - 1
                  if (lastIdx >= 0 && prev[lastIdx].status === 'running') {
                    const updated = [...prev]
                    updated[lastIdx] = {
                      ...updated[lastIdx],
                      status: 'stopped',
                      endTime: new Date().toISOString()
                    }
                    return updated
                  }
                  return prev
                })
              }
            } catch { /* 忽略 */ }
          }
        }
      }
    } catch (e) {
      console.error('测试失败:', e)
    } finally {
      setTestRunning(false)
    }
  }

  const stopTest = async () => {
    await fetch(`${BASE}/test/stop`, { method: 'POST' })
  }

  const continueTest = async () => {
    setTestStopped(false)
    setTestRunning(true)
    try {
      const resp = await fetch(`${BASE}/test/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dataset: testDataset, start_index: testResults.length }),
      })
      const reader = resp.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'result') {
                setTestResults(prev => [...prev, data])
                setTestProgress(Math.round(data.index / data.total * 100))
                // 更新当前测试的进度
                setTestHistory(prev => {
                  const lastIdx = prev.length - 1
                  if (lastIdx >= 0 && prev[lastIdx].status === 'running') {
                    const updated = [...prev]
                    updated[lastIdx] = {
                      ...updated[lastIdx],
                      progress: Math.round(data.index / data.total * 100),
                      total: data.total,
                      correct: prev[lastIdx].correct + (data.correct ? 1 : 0),
                      wrong: prev[lastIdx].wrong + (data.correct ? 0 : 1),
                      errors: prev[lastIdx].errors + (data.error ? 1 : 0)
                    }
                    return updated
                  }
                  return prev
                })
              } else if (data.type === 'done') {
                setTestSummary(data)
                setTestProgress(100)
                // 标记测试完成
                setTestHistory(prev => {
                  const lastIdx = prev.length - 1
                  if (lastIdx >= 0 && prev[lastIdx].status === 'running') {
                    const updated = [...prev]
                    updated[lastIdx] = {
                      ...updated[lastIdx],
                      status: 'completed',
                      endTime: new Date().toISOString(),
                      progress: 100,
                      total: data.total,
                      correct: data.correct,
                      wrong: data.wrong,
                      errors: data.errors
                    }
                    return updated
                  }
                  return prev
                })
              } else if (data.type === 'stopped') {
                setTestRunning(false)
                setTestStopped(true)
              }
            } catch {}
          }
        }
      }
    } catch (e) {
      console.error('测试失败:', e)
    } finally {
      setTestRunning(false)
    }
  }

  if (!token || !currentUser) return <Login onLogin={handleLogin} />

  return (
    <div className="app-layout">
      <Sidebar
        convs={convs}
        activeId={activeId}
        selectedDocs={selectedDocs}
        onSelectConv={handleSelectConv}
        onNewConv={handleNewConv}
        onDeleteConv={handleDeleteConv}
        onRenameConv={handleRenameConv}
        onTogglePin={handleTogglePin}
        onSelectDocs={setSelectedDocs}
        onRefresh={refreshConvs}
        token={token!}
        currentUser={currentUser}
        activePage={activePage}
        onChangePage={setActivePage}
        onLogout={handleLogout}
      />
      {activePage === 'admin' && currentUser.role === 'admin' ? (
        <AdminUsers token={token!} currentUser={currentUser} />
      ) : (
        <ChatView
          token={token!}
          activeId={activeId}
          selectedDocs={selectedDocs}
          onNewConv={handleNewConv}
          onRefresh={refreshConvs}
          onRemoveDoc={handleRemoveDoc}
          onClearDocs={handleClearDocs}
          testResults={testResults}
          testSummary={testSummary}
          testRunning={testRunning}
          testProgress={testProgress}
          testDataset={testDataset}
          onStopTest={stopTest}
          testStopped={testStopped}
          onContinueTest={continueTest}
          onRunTest={runTest}
          theme={theme}
          onToggleTheme={toggleTheme}
          testHistory={testHistory}
          testCurrentQid={testCurrentQid}
          testNextQid={testNextQid}
        />
      )}
    </div>
  )
}