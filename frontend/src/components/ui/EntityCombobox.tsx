import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, X } from 'lucide-react'
import type { HAEntity } from '@/types'

type EntityComboboxProps = {
  value: string
  onChange: (val: string) => void
  options: HAEntity[] | undefined
  placeholder?: string
  noneLabel?: string
  disabled?: boolean
  isLoading?: boolean
}

export function EntityCombobox({
  value,
  onChange,
  options,
  placeholder = 'Search entities...',
  noneLabel = 'None',
  disabled,
  isLoading,
}: EntityComboboxProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
        setSearch('')
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const selected = useMemo(
    () => (options ?? []).find((o) => o.entity_id === value),
    [options, value],
  )

  const displayValue = open
    ? search
    : selected
      ? `${selected.name} (${selected.entity_id})`
      : (value ?? '')

  const filtered = useMemo(() => {
    const list = options ?? []
    const q = search.trim().toLowerCase()
    if (!q) return list
    return list.filter(
      (o) =>
        o.entity_id.toLowerCase().includes(q) ||
        o.name.toLowerCase().includes(q),
    )
  }, [options, search])

  const inputClass =
    'flex h-11 w-full rounded-xl border border-input bg-transparent pl-4 pr-16 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-primary/40 dark:bg-[rgba(2,6,23,0.38)] dark:border-[rgba(148,163,184,0.22)] disabled:opacity-50'

  return (
    <div className="relative" ref={wrapRef}>
      <input
        type="text"
        value={displayValue}
        placeholder={placeholder}
        disabled={disabled}
        onFocus={() => {
          setSearch('')
          setOpen(true)
        }}
        onChange={(e) => {
          setSearch(e.target.value)
          if (!open) setOpen(true)
        }}
        className={inputClass}
      />
      <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
        {value && !open && (
          <button
            type="button"
            className="rounded p-1 text-muted-foreground hover:text-foreground"
            onClick={(e) => {
              e.preventDefault()
              onChange('')
            }}
            aria-label="Clear"
          >
            <X className="h-4 w-4" />
          </button>
        )}
        <button
          type="button"
          className="rounded p-1 text-muted-foreground hover:text-foreground"
          onClick={() => {
            setSearch('')
            setOpen((o) => !o)
          }}
          aria-label="Toggle"
        >
          <ChevronDown
            className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </button>
      </div>

      {open && (
        <div className="absolute z-50 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-border bg-background shadow-lg dark:border-[rgba(148,163,184,0.2)] dark:bg-[rgba(10,12,16,0.95)] dark:backdrop-blur-xl">
          <button
            type="button"
            className="block w-full px-3 py-2 text-left text-sm text-muted-foreground hover:bg-muted"
            onClick={() => {
              onChange('')
              setOpen(false)
              setSearch('')
            }}
          >
            {noneLabel}
          </button>
          {isLoading && (
            <div className="px-3 py-2 text-sm text-muted-foreground">Loading…</div>
          )}
          {!isLoading && filtered.length === 0 && (
            <div className="px-3 py-2 text-sm text-muted-foreground">
              {search
                ? `No entities matching "${search}"`
                : 'No entities available'}
            </div>
          )}
          {filtered.map((entity) => (
            <button
              key={entity.entity_id}
              type="button"
              className={`block w-full px-3 py-2 text-left text-sm hover:bg-muted ${
                entity.entity_id === value ? 'bg-muted/60' : ''
              }`}
              onClick={() => {
                onChange(entity.entity_id)
                setOpen(false)
                setSearch('')
              }}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{entity.name}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {entity.entity_id}
                    {entity.device_class ? ` · ${entity.device_class}` : ''}
                    {entity.unit_of_measurement
                      ? ` · ${entity.unit_of_measurement}`
                      : ''}
                  </div>
                </div>
                <span className="ml-2 shrink-0 text-xs text-muted-foreground">
                  {entity.state}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
