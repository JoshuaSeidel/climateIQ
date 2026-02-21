import { Moon, Sun } from 'lucide-react'
import { useUIStore } from '@/stores/uiStore'
import { cn } from '@/lib/utils'

export const ThemeToggle = () => {
  const { theme, toggleTheme } = useUIStore()

  return (
    <button
      aria-label="Toggle theme"
      onClick={toggleTheme}
      className={cn(
        'inline-flex h-10 w-10 items-center justify-center rounded-full border transition-all',
        'border-border/60 text-muted-foreground hover:text-foreground hover:bg-muted/60',
        'dark:border-[rgba(148,163,184,0.18)] dark:bg-[rgba(2,6,23,0.30)] dark:backdrop-blur-[10px]',
        'dark:hover:bg-white/5 dark:hover:shadow-[0_0_10px_rgba(56,189,248,0.12)]',
      )}
    >
      {theme === 'light' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  )
}
