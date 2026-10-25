import React, { MouseEvent } from 'react'

interface ThemeToggleProps {
  theme: 'light' | 'dark'
  onToggleTheme: () => void
}

export default function ThemeToggle({ theme, onToggleTheme }: ThemeToggleProps) {
  const handleClick = (e: MouseEvent<HTMLButtonElement>) => {
    e.preventDefault()
    console.log('Theme toggle button clicked, current theme:', theme)
    onToggleTheme()
  }

  return (
    <button 
      className="theme-toggle" 
      onClick={handleClick} 
      title={theme === 'light' ? '切换暗色模式' : '切换亮色模式'}
      type="button"
    >
      {theme === 'light' ? '🌙' : '☀️'}
    </button>
  )
}