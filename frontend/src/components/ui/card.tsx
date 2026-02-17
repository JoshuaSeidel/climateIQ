import { cn } from '@/lib/utils'

type CardProps = React.HTMLAttributes<HTMLDivElement>

export const Card = ({ className, ...props }: CardProps) => (
  <div className={cn('rounded-2xl border border-border/60 bg-card p-6 shadow-sm', className)} {...props} />
)

type CardHeaderProps = React.HTMLAttributes<HTMLDivElement>
export const CardHeader = ({ className, ...props }: CardHeaderProps) => (
  <div className={cn('space-y-1', className)} {...props} />
)

type CardTitleProps = React.HTMLAttributes<HTMLHeadingElement>
export const CardTitle = ({ className, ...props }: CardTitleProps) => (
  <h3 className={cn('text-lg font-semibold tracking-tight', className)} {...props} />
)

type CardDescriptionProps = React.HTMLAttributes<HTMLParagraphElement>
export const CardDescription = ({ className, ...props }: CardDescriptionProps) => (
  <p className={cn('text-sm text-muted-foreground', className)} {...props} />
)

type CardContentProps = React.HTMLAttributes<HTMLDivElement>
export const CardContent = ({ className, ...props }: CardContentProps) => (
  <div className={cn('mt-4 text-sm text-muted-foreground', className)} {...props} />
)
