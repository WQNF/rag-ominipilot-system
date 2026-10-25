import { useState, useEffect } from 'react'

interface Props {
  token: string
}

interface Settings {
  provider: string
  model: string
  api_key: string
  api_base: string
  temperature: number
  providers: Record<string, any>
  api_keys?: Record<string, string>
}

const PROVIDER_LABELS: Record<string, string> = {
  deepseek: 'DeepSeek',
  doubao: '豆包',
  minimax: 'Minimax',
  kimi: 'Kimi',
  other: '其他模型',
}

export default function ModelSwitcher({ token }: Props) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [expanded, setExpanded] = useState(false)
  
useEffect(() => {
    if (!token) return
    fetch('/settings', { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then(data => {
        setSettings(data)
      })
      .catch(() => {})
  }, [token])
  
  const switchModel = async (provider: string) => {
    if (!settings) return
    
    const providerPresets = {
      deepseek: { api_base: 'https://api.deepseek.com', model: 'deepseek-v4-flash' },
      doubao: { api_base: 'https://ark.cn-beijing.volces.com/api/v3', model: 'doubao-seed-evolving' },
      minimax: { api_base: 'https://api.minimaxi.com/anthropic', model: 'MiniMax-M2.7' },
      kimi: { api_base: 'https://api.moonshot.cn/v1', model: 'kimi-k2.7-code' },
    } as const
    
    const preset = providerPresets[provider as keyof typeof providerPresets]
    if (!preset) return
    
    try {
      await fetch('/settings', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          provider,
          model: preset.model,
          api_key: settings.api_keys?.[provider] || '',
          api_base: preset.api_base,
          temperature: settings.temperature,
          custom_model: '',
          custom_api_base: '',
        }),
      })
      
      // 刷新页面使配置生效
      window.location.reload()
    } catch (error) {
      console.error('切换模型失败:', error)
    }
  }
  
  if (!settings) return null
  
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{ padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--card-bg)', cursor: 'pointer', fontSize: 14, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 4 }}
      >
        <span>切换模型</span>
        <span style={{ fontSize: 12 }}>▼</span>
      </button>
      
      {expanded && (
        <div
          onClick={() => setExpanded(false)}
          style={{ position: 'absolute', top: '100%', right: 0, zIndex: 100, background: 'var(--card-bg)', border: '1px solid var(--border)', borderRadius: 8, boxShadow: '0 4px 12px rgba(0,0,0,0.15)', padding: 8, minWidth: 240 }}
        >
          <div onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8, padding: '0 4px', color: 'var(--text)' }}>快速切换模型</div>
            {Object.keys(PROVIDER_LABELS).map(provider => (
              <div
                key={provider}
                onClick={() => switchModel(provider)}
                style={{ 
                  padding: '8px 10px', 
                  borderRadius: 6, 
                  cursor: 'pointer', 
                  marginBottom: 4, 
                  color: 'var(--text)',
                  background: settings.api_keys?.[provider] ? 'rgba(76, 175, 80, 0.1)' : 'rgba(158, 158, 158, 0.1)',
                  border: settings.api_keys?.[provider] ? '1px solid rgba(76, 175, 80, 0.3)' : '1px solid rgba(158, 158, 158, 0.3)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span>{PROVIDER_LABELS[provider]}</span>
                  {settings.api_keys?.[provider] && <span style={{ color: '#4caf50', fontSize: 12, background: 'rgba(76, 175, 80, 0.2)', padding: '1px 4px', borderRadius: 3 }}>已配置</span>}
                </div>
                {!settings.api_keys?.[provider] && <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>未配置</span>}
              </div>
            ))}
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
              <button
                onClick={() => window.location.href = '#settings'}
                style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--card-bg)', cursor: 'pointer', fontSize: 14, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center' }}
              >
                ⚙️ 系统设置（管理API Key）
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}