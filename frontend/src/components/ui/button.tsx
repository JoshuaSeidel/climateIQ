import { forwardRef } from 'react'
import { type VariantProps, cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-xl text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60',
  {
    variants: {
      variant: {
        default:
          'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90 dark:bg-gradient-to-r dark:from-primary/90 dark:to-primary/70 dark:shadow-[0_0_15px_rgba(56,189,248,0.25)] dark:hover:shadow-[0_0_20px_rgba(56,189,248,0.35)]',
        outline:
          'border border-border bg-transparent text-foreground hover:bg-border/40 dark:border-[rgba(148,163,184,0.25)] dark:bg-[rgba(2,6,23,0.35)] dark:hover:bg-[rgba(2,6,23,0.55)]',
        ghost:
          'bg-transparent text-foreground hover:bg-foreground/10 dark:hover:bg-white/5',
        secondary:
          'bg-secondary text-secondary-foreground hover:bg-secondary/80',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 rounded-lg px-3',
        lg: 'h-11 rounded-xl px-8',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) => {
  return <button className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
})

Button.displayName = 'Button'
