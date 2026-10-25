import { useState, useEffect } from 'react'

interface Props {
  open: boolean
  onClose: () => void
  token: string
}

interface ProviderPresets {
  [key: string]: {
    api_base: string
    models: string[]
  }
}

interface Settings {
  provider: string
  model: string
  api_key: string
  api_base: string
  temperature: number
  custom_model: string
  custom_api_base: string
  providers: ProviderPresets
  api_keys: Record<string, string>
  retrieval_top_k?: number
  retrieval_nprobe?: number
  retrieval_threshold?: number
}

const PROVIDER_LABELS: Record<string, string> = {
  deepseek: 'DeepSeek',
  doubao: '豆包',
  minimax: 'Minimax',
  kimi: 'Kimi',
  other: '其他模型',
}

export default function SettingsPanel({ open, onClose, token }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

  const handleSettingsChange = (key: keyof Settings, value: any) => {
    if (!settings) return
    setSettings(prev => {
      if (!prev) return null
      return { ...prev, [key]: value }
    })
  }

  useEffect(() => {
    if (!open || !token) return
    fetch('/settings', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(data => {
        setSettings(data)
      })
      .catch(() => {
        // 加载失败不显示错误，使用默认设置
      })
  }, [open, token])

  if (!open) return null

  // 初始加载或加载失败时，使用默认设置
  const fallbackSettings: Settings = {
    provider: 'deepseek',
    model: 'deepseek-v4-flash',
    api_key: '',
    api_base: 'https://api.deepseek.com',
    temperature: 0.0,
    custom_model: '',
    custom_api_base: '',
    providers: {
      deepseek: { api_base: 'https://api.deepseek.com', models: ['deepseek-v4-flash', 'deepseek-v4-pro'] },
      doubao: { api_base: 'https://ark.cn-beijing.volces.com/api/v3', models: ['doubao-seed-evolving', 'doubao-seed-2-1-pro-260628', 'doubao-seed-2-1-turbo-260628'] },
      minimax: { api_base: 'https://api.minimaxi.com/anthropic', models: ['MiniMax-M2.7', 'MiniMax-M2.7-highspeed', 'MiniMax-M3'] },
      kimi: { api_base: 'https://api.moonshot.cn/v1', models: ['kimi-k2.7-code-highspeed', 'kimi-k2.7-code', 'kimi-k2.6'] },
      other: { api_base: '', models: ['custom'] },
    },
    api_keys: {},
    retrieval_top_k: 80,
    retrieval_nprobe: 16,
    retrieval_threshold: 0.3,
  }

  // 确保即使settings有部分字段缺失，也能正常显示
  // 优先从localStorage获取，然后是后端返回的settings，最后是fallbackSettings
  const savedSettings = localStorage.getItem('user_settings')
  const parsedSavedSettings = savedSettings ? JSON.parse(savedSettings) : {}
  
  const currentSettings = {
    ...fallbackSettings,
    ...settings,
    ...parsedSavedSettings,
    providers: {
      ...fallbackSettings.providers,
      ...settings?.providers,
    },
    api_keys: {
      ...fallbackSettings.api_keys,
      ...settings?.api_keys,
      ...parsedSavedSettings.api_keys,
    },
    retrieval_top_k: parsedSavedSettings.retrieval_top_k ?? settings?.retrieval_top_k ?? fallbackSettings.retrieval_top_k,
    retrieval_nprobe: parsedSavedSettings.retrieval_nprobe ?? settings?.retrieval_nprobe ?? fallbackSettings.retrieval_nprobe,
    retrieval_threshold: parsedSavedSettings.retrieval_threshold ?? settings?.retrieval_threshold ?? fallbackSettings.retrieval_threshold,
  }

  const providers = currentSettings.providers || fallbackSettings.providers
  const currentProvider = currentSettings.provider || fallbackSettings.provider
  const currentModels = (providers[currentProvider]?.models || fallbackSettings.providers[fallbackSettings.provider].models) as string[]

  const update = (field: string, value: string | number) => {
    const next = { ...currentSettings, [field]: value }
    // 切换提供商时，自动更新 api_base、model 和 api_key
    if (field === 'provider') {
      const preset = providers[value as string]
      if (preset) {
        next.api_base = preset.api_base
        next.model = preset.models[0] || ''
        next.custom_model = ''
        next.custom_api_base = ''
      } else {
        next.api_base = ''
        next.model = 'custom'
      }
      // 切换提供商时的API Key处理逻辑
      if (value === 'deepseek') {
        // 切换回deepseek时，自动填充已保存的api_key或当前设置的api_key
        next.api_key = currentSettings.api_keys?.[value as string] || currentSettings.api_key || ''
      } else {
        // 切换到非deepseek模型时，清除api_key
        next.api_key = ''
      }
    }
    // 自定义模型时，同步到 custom_model
    if (field === 'model' && currentProvider === 'other') {
      next.custom_model = value as string
    }
    // 自定义 api_base 时，同步到 custom_api_base
    if (field === 'api_base' && currentProvider === 'other') {
      next.custom_api_base = value as string
    }
    setSettings(next)
  }

  const handleSave = async () => {
    setSaving(true)
    setMsg('')
    try {
      // 保存当前提供商的 api_key 到 api_keys 对象
      const updatedApiKeys = {
        ...currentSettings.api_keys,
        [currentProvider]: currentSettings.api_key,
      }
      const res = await fetch('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          provider: currentProvider,
          model: currentSettings.model || currentModels[0],
          api_key: currentSettings.api_key,
          api_base: currentSettings.api_base,
          temperature: currentSettings.temperature,
          custom_model: currentSettings.custom_model,
          custom_api_base: currentSettings.custom_api_base,
          api_keys: updatedApiKeys,
          retrieval_top_k: currentSettings.retrieval_top_k,
          retrieval_nprobe: currentSettings.retrieval_nprobe,
          retrieval_threshold: currentSettings.retrieval_threshold,
        }),
      })

      // 超时处理
      const timeoutPromise = new Promise<Response>((_, reject) => {
        setTimeout(() => reject(new Error('请求超时')), 10000)
      })
      const response = await Promise.race([res, timeoutPromise])
      
      // 先检查响应状态，再尝试解析JSON
      if (response.ok) {
        let data: any
        try {
          data = await response.json()
        } catch {
          // 后端没有返回JSON，直接认为保存成功
          setMsg('保存成功')
        setSettings(prev => prev ? { 
          ...prev, 
          api_keys: updatedApiKeys,
          retrieval_top_k: currentSettings.retrieval_top_k,
          retrieval_nprobe: currentSettings.retrieval_nprobe,
          retrieval_threshold: currentSettings.retrieval_threshold,
        } : null)
        
        // 保存到localStorage
        localStorage.setItem('user_settings', JSON.stringify({
          provider: currentProvider,
          model: currentSettings.model || currentModels[0],
          api_key: currentSettings.api_key,
          api_base: currentSettings.api_base,
          temperature: currentSettings.temperature,
          custom_model: currentSettings.custom_model,
          custom_api_base: currentSettings.custom_api_base,
          api_keys: updatedApiKeys,
          retrieval_top_k: currentSettings.retrieval_top_k,
          retrieval_nprobe: currentSettings.retrieval_nprobe,
          retrieval_threshold: currentSettings.retrieval_threshold,
        }))
        
        // 通知其他页面模型已更新
        localStorage.setItem('model_updated', Date.now().toString())
        
        setTimeout(() => { setMsg(''); onClose() }, 1500)
          return
        }
        setMsg(data.message || '保存成功')
        setSettings(prev => prev ? { ...prev, api_keys: updatedApiKeys } : null)
        
        // 通知其他页面模型已更新
        localStorage.setItem('model_updated', Date.now().toString())
        
        setTimeout(() => { setMsg(''); onClose() }, 1500)
      } else {
        setMsg('保存失败')
      }
    } catch (error: unknown) {
      const errMsg = (error as Error).message || '网络错误'
      // 处理JSON解析错误
      if (errMsg.includes('JSON input')) {
        setMsg('保存成功')
        
        // 通知其他页面模型已更新
        localStorage.setItem('model_updated', Date.now().toString())
        
        setTimeout(() => { setMsg(''); onClose() }, 1500)
      } else {
        setMsg(errMsg)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        background: 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 680,
          maxHeight: '80vh',
          background: 'var(--card-bg)',
          borderRadius: 12,
          padding: 20,
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.3)',
          overflowY: 'auto',
          color: 'var(--text)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <h3 style={{ margin: 0, fontSize: 22 }}>⚙️ 系统设置</h3>
          <button
            onClick={onClose}
            style={{ width: 36, height: 36, border: 'none', background: 'var(--thinking-bg)', borderRadius: 4, cursor: 'pointer', fontSize: 20, padding: 0, lineHeight: 1, color: 'var(--text)' }}
          >
            ✕
          </button>
        </div>

        {/* ── LLM 模型配置 ── */}
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text)', marginBottom: 10, borderBottom: '1px solid var(--border)', paddingBottom: 4 }}>
            🤖 LLM 模型配置
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '10px 12px', fontSize: 15, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-secondary)' }}>模型提供商</span>
            <select
              value={currentProvider}
              onChange={e => update('provider', e.target.value)}
              style={{ padding: '5px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 15, background: 'var(--card-bg)', color: 'var(--text)' }}
            >
              {Object.keys(providers).map(k => (
                <option key={k} value={k}>{PROVIDER_LABELS[k] || k}</option>
              ))}
            </select>

            <span style={{ color: 'var(--text-secondary)' }}>模型名称</span>
            {currentProvider === 'other' ? (
              <input
                type="text"
                value={currentSettings.custom_model || currentSettings.model || ''}
                onChange={e => update('model', e.target.value)}
                placeholder="请输入模型名称"
                style={{ padding: '5px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 15, background: 'var(--card-bg)', color: 'var(--text)' }}
              />
            ) : (
              <select
                value={currentSettings.model || currentModels[0]}
                onChange={e => update('model', e.target.value)}
                style={{ padding: '5px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 15, background: 'var(--card-bg)', color: 'var(--text)' }}
              >
                {currentModels.map((m: string) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            )}

            <span style={{ color: 'var(--text-secondary)' }}>API Key</span>
            <input
              type="password"
              value={currentSettings.api_key || ''}
              onChange={e => update('api_key', e.target.value)}
              placeholder={`${PROVIDER_LABELS[currentProvider]} 的 API Key`}
              style={{ padding: '5px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 15, background: 'var(--card-bg)', color: 'var(--text)' }}
            />

            {currentProvider === 'other' && (
              <>
                <span style={{ color: 'var(--text-secondary)' }}>API Base URL</span>
                <input
                  type="text"
                  value={currentSettings.custom_api_base || currentSettings.api_base || ''}
                  onChange={e => update('api_base', e.target.value)}
                  placeholder="https://api.example.com"
                  style={{ padding: '5px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 15, background: 'var(--card-bg)', color: 'var(--text)' }}
                />
              </>
            )}

            <span style={{ color: 'var(--text-secondary)' }}>Temperature</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={currentSettings.temperature || 0}
                onChange={e => update('temperature', parseFloat(e.target.value))}
                style={{ flex: 1 }}
              />
              <span style={{ minWidth: 30, textAlign: 'right', color: 'var(--text-secondary)' }}>{currentSettings.temperature || 0}</span>
            </div>
          </div>

          {/* 提示信息 */}
          {currentProvider === 'other' && (
            <div style={{ padding: '8px 12px', marginTop: 10, borderRadius: 4, background: '#fff3e0', color: '#ef6c00', fontSize: 14 }}>
              ⚠️ 提示：选择其他模型需自行准备模型名称、API Base 和 API Key
            </div>
          )}
        </div>

        {/* ── 检索参数 ── */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginBottom: 8, borderBottom: '1px solid var(--border)', paddingBottom: 4 }}>
            🔍 检索参数配置
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '8px 10px', fontSize: 13, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Top-K 召回</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="range"
                min="5"
                max="100"
                step="5"
                value={currentSettings.retrieval_top_k || 80}
                onChange={e => update('retrieval_top_k', parseInt(e.target.value))}
                style={{ flex: 1 }}
              />
              <span style={{ minWidth: 30, textAlign: 'right', color: 'var(--text-secondary)' }}>{currentSettings.retrieval_top_k || 80}</span>
            </div>
            <span style={{ color: 'var(--text-secondary)' }}>nprobe 精度</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="range"
                min="4"
                max="64"
                step="4"
                value={currentSettings.retrieval_nprobe || 16}
                onChange={e => update('retrieval_nprobe', parseInt(e.target.value))}
                style={{ flex: 1 }}
              />
              <span style={{ minWidth: 30, textAlign: 'right', color: 'var(--text-secondary)' }}>{currentSettings.retrieval_nprobe || 16}</span>
            </div>
            <span style={{ color: 'var(--text-secondary)' }}>相似度阈值</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={currentSettings.retrieval_threshold || 0.3}
                onChange={e => update('retrieval_threshold', parseFloat(e.target.value))}
                style={{ flex: 1 }}
              />
              <span style={{ minWidth: 30, textAlign: 'right', color: 'var(--text-secondary)' }}>{currentSettings.retrieval_threshold || 0.3}</span>
            </div>
          </div>
        </div>





        {/* ── 知识库管理 ── */}
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)', marginBottom: 8, borderBottom: '1px solid var(--border)', paddingBottom: 4 }}>
            🗄️ 知识库管理
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '8px 10px', fontSize: 13, alignItems: 'center' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Milvus</span>
            <input type="text" placeholder="localhost:19530" style={{ padding: '5px 10px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 15, background: 'var(--card-bg)', color: 'var(--text)' }} />
            <span style={{ color: 'var(--text-secondary)' }}>Embedding</span>
            <select style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', fontSize: 13, background: 'var(--card-bg)', color: 'var(--text)' }}>
              <option>Qwen/Qwen3-Embedding-0.6B</option>
            </select>
          </div>
        </div>

        {/* ── 底部按钮 ── */}
        {msg && (
          <div style={{ padding: '8px 12px', marginBottom: 10, borderRadius: 4, background: msg === '设置已保存并生效' || msg === '保存成功' ? '#e8f5e9' : '#ffebee', color: msg === '设置已保存并生效' || msg === '保存成功' ? '#2e7d32' : '#c62828', fontSize: 14 }}>
            {msg}
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', paddingTop: 10, borderTop: '1px solid var(--border)' }}>
          <button
            onClick={onClose}
            style={{ padding: '8px 20px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--card-bg)', cursor: 'pointer', fontSize: 15, color: 'var(--text)' }}
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            style={{ padding: '8px 20px', borderRadius: 4, border: '1px solid #2196f3', background: '#2196f3', color: '#fff', cursor: saving ? 'not-allowed' : 'pointer', fontSize: 15, opacity: saving ? 0.7 : 1 }}
          >
            {saving ? '⏳ 保存中…' : '💾 保存设置'}
          </button>
        </div>
      </div>
    </div>
  )
}