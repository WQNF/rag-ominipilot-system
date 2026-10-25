import { useEffect, useState, FormEvent } from 'react'
import {
  createAdminUser,
  deleteAdminUser,
  listAdminUsers,
  updateAdminUser,
} from '../api/client'
import type { AdminUserUpdatePayload, AuthUser, PublicUser, UserRole } from '../types'

interface Props {
  token: string
  currentUser: AuthUser
}

interface UserFormState {
  username: string
  password: string
  confirmPassword: string
  role: UserRole
  full_name: string
  department: string
  email: string
  is_active: boolean
}

const emptyForm: UserFormState = {
  username: '',
  password: '',
  confirmPassword: '',
  role: 'user',
  full_name: '',
  department: '',
  email: '',
  is_active: true,
}

function cleanOptional(value: string): string | undefined {
  const trimmed = value.trim()
  return trimmed || undefined
}

function validatePassword(password: string): string {
  if (password.length < 6) return '密码长度不能少于 6 位'
  if (/^\d+$/.test(password)) return '密码不能是纯数字'
  return ''
}

export default function AdminUsers({ token, currentUser }: Props) {
  const [users, setUsers] = useState<PublicUser[]>([])
  const [form, setForm] = useState<UserFormState>(emptyForm)
  const [editing, setEditing] = useState<string | null>(null)
  const [editForm, setEditForm] = useState<UserFormState>(emptyForm)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const refreshUsers = async () => {
    setLoading(true)
    setError('')
    try {
      setUsers(await listAdminUsers(token))
    } catch (err: any) {
      setError(err.message || '获取用户列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshUsers()
  }, [token])

  const updateForm = (key: keyof UserFormState, value: string | boolean) => {
    setForm(prev => ({ ...prev, [key]: value }))
  }

  const updateEditForm = (key: keyof UserFormState, value: string | boolean) => {
    setEditForm(prev => ({ ...prev, [key]: value }))
  }

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault()
    if (!form.username.trim() || !form.password) {
      setError('请输入用户名和密码')
      return
    }
    if (form.password !== form.confirmPassword) {
      setError('两次输入的密码不一致')
      return
    }
    const passwordError = validatePassword(form.password)
    if (passwordError) {
      setError(passwordError)
      return
    }
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await createAdminUser(token, {
        username: form.username.trim(),
        password: form.password,
        confirm_password: form.confirmPassword,
        role: form.role,
        full_name: cleanOptional(form.full_name),
        department: cleanOptional(form.department),
        email: cleanOptional(form.email),
        is_active: form.is_active,
      })
      setForm(emptyForm)
      setMessage('用户新增成功')
      await refreshUsers()
    } catch (err: any) {
      setError(err.message || '新增用户失败')
    } finally {
      setSaving(false)
    }
  }

  const startEdit = (user: PublicUser) => {
    setEditing(user.username)
    setEditForm({
      username: user.username,
      password: '',
      confirmPassword: '',
      role: user.role,
      full_name: user.full_name || '',
      department: user.department || '',
      email: user.email || '',
      is_active: user.is_active,
    })
    setError('')
    setMessage('')
  }

  const handleSave = async (username: string) => {
    if (editForm.password && editForm.password !== editForm.confirmPassword) {
      setError('两次输入的新密码不一致')
      return
    }
    if (editForm.password) {
      const passwordError = validatePassword(editForm.password)
      if (passwordError) {
        setError(passwordError)
        return
      }
    }
    setSaving(true)
    setError('')
    setMessage('')
    try {
      const payload: AdminUserUpdatePayload = {
        role: editForm.role,
        full_name: cleanOptional(editForm.full_name),
        department: cleanOptional(editForm.department),
        email: cleanOptional(editForm.email),
        is_active: editForm.is_active,
      }
      if (editForm.password) {
        payload.password = editForm.password
        payload.confirm_password = editForm.confirmPassword
      }
      await updateAdminUser(token, username, payload)
      setEditing(null)
      setMessage('用户信息已更新')
      await refreshUsers()
    } catch (err: any) {
      setError(err.message || '更新用户失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (username: string) => {
    if (username === currentUser.username) {
      setError('不能删除当前登录的管理员账号')
      return
    }
    if (!window.confirm(`确认删除用户 ${username} 吗？`)) return
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await deleteAdminUser(token, username)
      setMessage('用户已删除')
      await refreshUsers()
    } catch (err: any) {
      setError(err.message || '删除用户失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="admin-page">
      <div className="admin-header">
        <div>
          <h2>用户管理</h2>
          <p>管理员可以在这里新增、编辑、停用或删除系统用户。</p>
        </div>
        <button onClick={refreshUsers} disabled={loading || saving}>
          {loading ? '刷新中...' : '刷新列表'}
        </button>
      </div>

      <form className="admin-card admin-create-form" onSubmit={handleCreate}>
        <h3>新增用户</h3>
        <div className="admin-form-grid">
          <label>
            用户名
            <input value={form.username} onChange={e => updateForm('username', e.target.value)} placeholder="例如 lisi" />
          </label>
          <label>
            密码
            <input type="password" value={form.password} onChange={e => updateForm('password', e.target.value)} placeholder="请输入密码" />
          </label>
          <label>
            确认密码
            <input type="password" value={form.confirmPassword} onChange={e => updateForm('confirmPassword', e.target.value)} placeholder="再次输入密码" />
          </label>
          <div className="admin-password-rule">密码至少 6 位，且不能是纯数字</div>
          <label>
            角色
            <select value={form.role} onChange={e => updateForm('role', e.target.value as UserRole)}>
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
          </label>
          <label>
            姓名
            <input value={form.full_name} onChange={e => updateForm('full_name', e.target.value)} placeholder="可选" />
          </label>
          <label>
            部门
            <input value={form.department} onChange={e => updateForm('department', e.target.value)} placeholder="可选" />
          </label>
          <label>
            邮箱
            <input value={form.email} onChange={e => updateForm('email', e.target.value)} placeholder="可选" />
          </label>
          <label className="admin-checkbox">
            <input type="checkbox" checked={form.is_active} onChange={e => updateForm('is_active', e.target.checked)} />
            启用账号
          </label>
        </div>
        <button className="admin-primary" type="submit" disabled={saving}>
          {saving ? '保存中...' : '新增用户'}
        </button>
      </form>

      {(message || error) && (
        <div className={`admin-message ${error ? 'error' : 'success'}`}>
          {error || message}
        </div>
      )}

      <div className="admin-card">
        <div className="admin-table-header">
          <h3>用户列表</h3>
          <span>共 {users.length} 个账号</span>
        </div>

        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>用户名</th>
                <th>角色</th>
                <th>姓名</th>
                <th>部门</th>
                <th>邮箱</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map(user => {
                const isSelf = user.username === currentUser.username
                const isEditing = editing === user.username
                return (
                  <tr key={user.username}>
                    <td>
                      <strong>{user.username}</strong>
                      {isSelf && <span className="admin-self-tag">当前账号</span>}
                    </td>
                    <td>
                      {isEditing ? (
                        <select value={editForm.role} onChange={e => updateEditForm('role', e.target.value as UserRole)}>
                          <option value="user">普通用户</option>
                          <option value="admin">管理员</option>
                        </select>
                      ) : (
                        user.role === 'admin' ? '管理员' : '普通用户'
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <input value={editForm.full_name} onChange={e => updateEditForm('full_name', e.target.value)} />
                      ) : (
                        user.full_name || '-'
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <input value={editForm.department} onChange={e => updateEditForm('department', e.target.value)} />
                      ) : (
                        user.department || '-'
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <input value={editForm.email} onChange={e => updateEditForm('email', e.target.value)} />
                      ) : (
                        user.email || '-'
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <label className="admin-inline-checkbox">
                          <input type="checkbox" checked={editForm.is_active} onChange={e => updateEditForm('is_active', e.target.checked)} />
                          启用
                        </label>
                      ) : (
                        <span className={`admin-status ${user.is_active ? 'active' : 'disabled'}`}>
                          {user.is_active ? '启用' : '停用'}
                        </span>
                      )}
                    </td>
                    <td>
                      {isEditing ? (
                        <div className="admin-actions vertical">
                          <input
                            type="password"
                            value={editForm.password}
                            onChange={e => updateEditForm('password', e.target.value)}
                            placeholder="新密码，可不填"
                          />
                          <input
                            type="password"
                            value={editForm.confirmPassword}
                            onChange={e => updateEditForm('confirmPassword', e.target.value)}
                            placeholder="确认新密码"
                          />
                          <span className="admin-password-rule compact">新密码至少 6 位，且不能是纯数字</span>
                          <div>
                            <button onClick={() => handleSave(user.username)} disabled={saving}>保存</button>
                            <button className="ghost" onClick={() => setEditing(null)} disabled={saving}>取消</button>
                          </div>
                        </div>
                      ) : (
                        <div className="admin-actions">
                          <button onClick={() => startEdit(user)} disabled={saving}>编辑</button>
                          <button className="danger" onClick={() => handleDelete(user.username)} disabled={saving || isSelf}>删除</button>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
              {!loading && users.length === 0 && (
                <tr>
                  <td colSpan={7} className="admin-empty">暂无用户</td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td colSpan={7} className="admin-empty">正在加载用户列表...</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
