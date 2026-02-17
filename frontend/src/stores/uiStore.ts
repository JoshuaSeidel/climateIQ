import { create } from 'zustand'

type UIState = {
  theme: 'light' | 'dark'
  sidebarOpen: boolean
  toggleTheme: () => void
  setTheme: (theme: 'light' | 'dark') => void
  toggleSidebar: () => void
  setSidebarOpen: (open: boolean) => void
}

const prefersDark = typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches

// Apply initial theme class to DOM
if (typeof document !== 'undefined') {
  document.documentElement.classList.toggle('dark', prefersDark)
}

export const useUIStore = create<UIState>((set) => ({
  theme: prefersDark ? 'dark' : 'light',
  sidebarOpen: typeof window !== 'undefined' ? window.innerWidth >= 1024 : false,
  toggleTheme: () =>
    set((state) => {
      const next = state.theme === 'light' ? 'dark' : 'light'
      document.documentElement.classList.toggle('dark', next === 'dark')
      return { theme: next }
    }),
  setTheme: (theme) => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    set({ theme })
  },
  toggleSidebar: () =>
    set((state) => ({
      sidebarOpen: !state.sidebarOpen,
    })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
}))
