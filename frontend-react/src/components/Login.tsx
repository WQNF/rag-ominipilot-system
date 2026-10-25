
import { useState, FormEvent } from 'react'
import { login, registerUser } from '../api/client'
import type { LoginResponse } from '../types'

interface Props {
  onLogin: (session: LoginResponse) => void
}

function validatePassword(password: string): string {
  if (password.length < 6) return '密码长度不能少于 6 位'
  if (/^\d+$/.test(password)) return '密码不能是纯数字'
  return ''
}

export default function Login({ onLogin }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [department, setDepartment] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username || !password) {
      setError('请输入用户名和密码')
      return
    }
    if (mode === 'register' && password !== confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    if (mode === 'register') {
      const passwordError = validatePassword(password)
      if (passwordError) {
        setError(passwordError)
        return
      }
    }
    setLoading(true)
    setError('')
    try {
      if (mode === 'register') {
        await registerUser({
          username,
          password,
          confirm_password: confirmPassword,
          full_name: fullName.trim() || undefined,
          department: department.trim() || undefined,
        })
      }
      const session = await login(username, password)
      onLogin(session)
    } catch (err: any) {
      setError(err.message || (mode === 'register' ? '注册失败，请检查后端是否启动' : '登录失败，请检查后端是否启动'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <h1>第五届研究生金融科技创新大赛</h1>
      <h3>🔐 面向银行业监管制度与统计报表的可信RAG问答系统</h3>
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="auth-tabs">
          <button
            type="button"
            className={mode === 'login' ? 'active' : ''}
            onClick={() => {
              setMode('login')
              setError('')
            }}
          >
            登录
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'active' : ''}
            onClick={() => {
              setMode('register')
              setError('')
            }}
          >
            注册
          </button>
        </div>

        <label>用户名</label>
        <input
          type="text" value={username}
          onChange={e => setUsername(e.target.value)}
          placeholder="请输入用户名" autoFocus
        />
        <label>密码</label>
        <input
          type="password" value={password}
          onChange={e => setPassword(e.target.value)}
          placeholder="请输入密码"
        />

        {mode === 'register' && (
          <>
            <label>确认密码</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              placeholder="请再次输入密码"
            />
            <div className="password-rule">密码至少 6 位，且不能是纯数字</div>
            <label>姓名（可选）</label>
            <input
              type="text"
              value={fullName}
              onChange={e => setFullName(e.target.value)}
              placeholder="请输入姓名"
            />
            <label>部门（可选）</label>
            <input
              type="text"
              value={department}
              onChange={e => setDepartment(e.target.value)}
              placeholder="请输入部门"
            />
          </>
        )}

        <button type="submit" disabled={loading}>
          {loading ? (mode === 'register' ? '注册中...' : '登录中...') : (mode === 'register' ? '注册并登录' : '登录')}
        </button>
        {error && <div className="login-error">{error}</div>}
      </form>
    </div>
  )
}
