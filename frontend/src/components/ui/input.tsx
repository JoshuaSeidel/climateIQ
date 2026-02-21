import { forwardRef, type InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export type InputProps = InputHTMLAttributes<HTMLInputElement>

export const Input = forwardRef<HTMLInputElement, InputProps>(({ className, type = 'text', ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        'flex h-11 w-full rounded-xl border border-input bg-transparent px-4 text-base ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 sm:text-sm',
        'dark:bg-[rgba(2,6,23,0.38)] dark:border-[rgba(148,163,184,0.22)] dark:focus-visible:ring-primary/40 dark:focus-visible:shadow-[0_0_10px_rgba(56,189,248,0.15)]',
        className,
      )}
      ref={ref}
      {...props}
    />
  )
})

Input.displayName = 'Input'
