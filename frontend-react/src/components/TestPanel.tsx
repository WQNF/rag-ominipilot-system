import { useState, useEffect } from 'react'

const BASE = ''

interface Dataset {
  name: string
  count: number
  size: number
}

interface TestHistoryItem {
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
}

interface Props {
  testRunning: boolean
  testProgress: number
  onRunTest: (dataset: string, startQid?: string, testId?: string) => void
  onStopTest: () => void
  testStopped: boolean
  onContinueTest: () => void
  currentQid: string
  nextQid: string
  testHistory: TestHistoryItem[]
}

export default function TestPanel({ testRunning, testProgress, onRunTest, onStopTest, testStopped, onContinueTest, currentQid, nextQid, testHistory }: Props) {
  // 监听删除测试记录事件 - 已移至App.tsx处理
  useEffect(() => {
    const handleDelete = (e: CustomEvent) => {
      // 触发全局事件，由App.tsx处理删除
      window.dispatchEvent(new CustomEvent('delete-test-history', { detail: e.detail }))
    }
    window.addEventListener('delete-test-history', handleDelete as EventListener)
    return () => window.removeEventListener('delete-test-history', handleDelete as EventListener)
  }, [])
  const [expanded, setExpanded] = useState(false)
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [selected, setSelected] = useState('all.py')
  const [currentTestId, setCurrentTestId] = useState<string>('')
  const [historyExpanded, setHistoryExpanded] = useState(false)

  const fetchDatasets = async () => {
    try {
      const resp = await fetch(`${BASE}/test/datasets`)
      if (resp.ok) {
        const list: Dataset[] = await resp.json()
        setDatasets(list)
        if (list.length > 0 && !list.find(d => d.name === selected)) {
          setSelected(list[0].name)
        }
      }
    } catch { /* 忽略 */ }
  }

  const toggle = () => {
    if (!expanded && datasets.length === 0) fetchDatasets()
    setExpanded(!expanded)
  }

  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: '8px' }}>
      <div
        onClick={toggle}
        style={{ fontSize: 15, fontWeight: 600, color: 'var(--text)', padding: '4px 4px', cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 4 }}
      >
        {expanded ? '▼' : '▶'} 🧪 快速测试面板
      </div>
      {expanded && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 8, marginTop: 8, background: 'var(--thinking-bg)' }}>

        <div style={{ marginTop: 8, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <select
            value={selected}
            onChange={e => setSelected(e.target.value)}
            style={{ flex: 1, padding: '4px', fontSize: 12, borderRadius: 4, border: '1px solid var(--border)' }}
          >
            {datasets.map(d => (
              <option key={d.name} value={d.name}>{d.name} ({d.count}题)</option>
            ))}
          </select>
          <button
            onClick={() => onRunTest(selected)}
            disabled={testRunning}
            style={{ padding: '4px 10px', fontSize: 12, whiteSpace: 'nowrap' }}
          >
            {testRunning ? '⏳' : '▶️'} 运行
          </button>
          {testRunning && (
            <button
              onClick={onStopTest}
              title={currentQid ? `当前正在运行 ${currentQid}，此题目运行完后停止` : '正在运行第一道题目，此题目运行完后停止'}
              style={{ padding: '4px 10px', fontSize: 12, whiteSpace: 'nowrap', background: '#e74c3c', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
            >
              ⏹️ 停止
            </button>
          )}
          {testStopped && (
            <button
              onClick={onContinueTest}
              title={nextQid ? `点击继续从 ${nextQid} 开始` : ''}
              style={{ padding: '4px 10px', fontSize: 12, whiteSpace: 'nowrap', background: '#4caf50', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
            >
              ▶️ 继续
            </button>
          )}
        </div>

        {testRunning && (
          <div style={{ marginTop: 8 }}>
            <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${testProgress}%`, background: '#4caf50', transition: 'width 0.3s' }} />
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'center', marginTop: 2 }}>{testProgress}%</div>
          </div>
        )}

        {/* 测试历史记录 */}
        <div style={{ marginTop: 12 }}>
          <div
            onClick={() => setHistoryExpanded(!historyExpanded)}
            style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', padding: '4px 4px', cursor: 'pointer', userSelect: 'none', display: 'flex', alignItems: 'center', gap: 4 }}
          >
            {historyExpanded ? '▼' : '▶'} 📋 测试历史记录 ({testHistory.length})
          </div>
          {historyExpanded && testHistory.length > 0 && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 6, padding: 6, marginTop: 4, background: 'var(--thinking-bg)', maxHeight: 200, overflowY: 'auto' }}>
              {testHistory.slice().reverse().map(item => (
                <div key={item.id} style={{ padding: '4px 6px', marginBottom: 4, borderBottom: '1px solid var(--border)', borderRadius: 4, background: item.status === 'running' ? 'rgba(76, 175, 80, 0.1)' : item.status === 'completed' ? 'rgba(33, 150, 243, 0.1)' : 'rgba(231, 76, 60, 0.1)' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>
                    {item.dataset.replace('.py', '')}
                    <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
                      {item.status === 'running' ? '⏳ 运行中' : item.status === 'completed' ? '✅ 已完成' : '⏹️ 已停止'}
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                    {new Date(item.startTime).toLocaleString('zh-CN')}
                    {item.endTime && ` - ${new Date(item.endTime).toLocaleString('zh-CN')}`}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2, display: 'flex', gap: 12 }}>
                    <span>进度: {item.progress}%</span>
                    <span>总题数: {item.total}</span>
                    <span>正确: {item.correct}</span>
                    <span>错误: {item.wrong}</span>
                    <span>异常: {item.errors}</span>
                  </div>
                  {item.status !== 'running' && (
                    <div style={{ fontSize: 11, marginTop: 2 }}>
                      准确率: {item.total > 0 ? Math.round((item.correct / item.total) * 100) : 0}%
                    </div>
                  )}
                  {/* 状态提示 */}
                  <div 
                    style={{
                      backgroundColor: item.status === 'completed' ? '#4caf50' : item.status === 'stopped' ? '#ff9800' : '#2196f3',
                      fontSize: 11,
                      marginTop: 3,
                      padding: '2px 6px',
                      borderRadius: 3,
                      fontWeight: 600,
                      color: '#fff',
                      textAlign: 'center'
                    }}
                  >
                    {item.status === 'completed' ? '✅ 全部完成' : item.status === 'stopped' ? `⏸️ 中断（已完成${item.correct + item.wrong + item.errors}题）` : '⏳ 运行中'}
                  </div>
                  {/* 操作按钮 - 靠右对齐 */}
                  <div style={{ marginTop: 6, display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
                    {/* 所有状态都显示再次测试按钮 */}
                    <button
                      onClick={() => onRunTest(item.dataset)}
                      style={{ padding: '2px 8px', fontSize: 11, backgroundColor: '#2196f3', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
                    >
                      🔄 再次测试
                    </button>
                    {/* 中断状态显示继续测试按钮 - 只要是stopped状态就显示，不依赖nextQid */}
                    {item.status === 'stopped' && (
                      <button
                        onClick={() => onRunTest(item.dataset, item.nextQid || undefined, item.id)}
                        style={{ padding: '2px 8px', fontSize: 11, backgroundColor: '#4caf50', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
                      >
                        ▶️ 继续测试
                      </button>
                    )}
                    {/* 所有状态都显示删除按钮 */}
                    <button
                      onClick={() => {
                        if (window.confirm('确定要删除这条测试记录吗？')) {
                          // 触发全局删除事件
                          window.dispatchEvent(new CustomEvent('delete-test-history', { detail: item.id }))
                        }
                      }}
                      style={{ padding: '2px 8px', fontSize: 11, backgroundColor: '#f44336', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
                    >
                      🗑️ 删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {historyExpanded && testHistory.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', textAlign: 'center', padding: 12 }}>
              暂无测试记录
            </div>
          )}
        </div>
        </div>
      )}
    </div>
  )
}