
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/token': 'http://127.0.0.1:8000',
      '/chat': 'http://127.0.0.1:8000',
      '/files': 'http://127.0.0.1:8000',
      '/register': 'http://127.0.0.1:8000',
      '/admin': 'http://127.0.0.1:8000',
      '/settings': 'http://127.0.0.1:8000',
      '/knowledge': 'http://127.0.0.1:8000',
      '/test': 'http://127.0.0.1:8000',
      '/users': 'http://127.0.0.1:8000',
      '/metrics': 'http://127.0.0.1:8000',
    }
  }
})