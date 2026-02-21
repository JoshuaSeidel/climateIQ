import { cn } from '@/lib/utils'

type CardProps = React.HTMLAttributes<HTMLDivElement>

export const Card = ({ className, ...props }: CardProps) => (
  <div
    className={cn(
      'rounded-2xl border border-border/30 bg-card/80 p-4 shadow-sm sm:p-6',
      'backdrop-blur-xl',
      'dark:bg-[rgba(10,12,16,0.62)] dark:border-[rgba(148,163,184,0.18)] dark:shadow-[0_0_15px_rgba(148,163,184,0.06)]',
      className,
    )}
    {...props}
  />
)

type CardHeaderProps = React.HTMLAttributes<HTMLDivElement>
export const CardHeader = ({ className, ...props }: CardHeaderProps) => (
  <div className={cn('space-y-1', className)} {...props} />
)

type CardTitleProps = React.HTMLAttributes<HTMLHeadingElement>
export const CardTitle = ({ className, ...props }: CardTitleProps) => (
  <h3 className={cn('text-lg font-bold tracking-tight', className)} {...props} />
)

type CardDescriptionProps = React.HTMLAttributes<HTMLParagraphElement>
export const CardDescription = ({ className, ...props }: CardDescriptionProps) => (
  <p className={cn('text-sm text-muted-foreground', className)} {...props} />
)

type CardContentProps = React.HTMLAttributes<HTMLDivElement>
export const CardContent = ({ className, ...props }: CardContentProps) => (
  <div className={cn('mt-4 text-sm text-muted-foreground', className)} {...props} />
)
