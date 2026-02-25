import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api, BASE_PATH } from '@/lib/api'
import type { ChatAction, ChatMessage, ChatResponse, ConversationHistoryItem, UserDirective } from '@/types'
import {
  MessageSquarePlus,
  Send,
  Bot,
  User,
  Sparkles,
  Loader2,
  Trash2,
  MessageCircle,
  X,
  ChevronLeft,
  ChevronRight,
  Brain,
} from 'lucide-react'

const SUGGESTIONS = [
  "What's the temperature in the living room?",
  'Set all zones to eco mode',
  "Show me today's energy usage",
  'Make the bedroom cooler',
  "What's the current schedule?",
  'How can I save energy?',
]

export const Chat = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: '1',
      role: 'assistant',
      content:
        "Hello! I'm ClimateIQ, your intelligent HVAC assistant. I can help you control temperatures, set schedules, and optimize your home's climate. What would you like to do?",
      timestamp: new Date(),
    },
  ])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 1024 : false
  )
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Fetch conversation history
  const { data: conversations, refetch: refetchConversations } = useQuery<ConversationHistoryItem[]>({
    queryKey: ['chat-history'],
    queryFn: () => api.get<ConversationHistoryItem[]>('/chat/history', { limit: 50 }),
  })

  // Fetch user directives (memory)
  const { data: directives, refetch: refetchDirectives } = useQuery<UserDirective[]>({
    queryKey: ['chat-directives'],
    queryFn: () => api.get<UserDirective[]>('/chat/directives', { active_only: true }),
  })

  // Group conversations by session_id
  const conversationSessions = useMemo(() => {
    if (!conversations?.length) return []
    const sessions = new Map<string, { session_id: string; first_message: string; created_at: string; count: number }>()
    for (const conv of conversations) {
      if (!sessions.has(conv.session_id)) {
        sessions.set(conv.session_id, {
          session_id: conv.session_id,
          first_message: conv.user_message,
          created_at: conv.created_at,
          count: 1,
        })
      } else {
        sessions.get(conv.session_id)!.count++
      }
    }
    return Array.from(sessions.values()).sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    )
  }, [conversations])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  // Send message mutation
  const sendMessage = useMutation({
    mutationFn: async (message: string) => {
      const response = await fetch(`${BASE_PATH}/api/v1/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          session_id: sessionId,
        }),
      })
      if (!response.ok) {
        throw new Error('Failed to send message')
      }
      return response.json() as Promise<ChatResponse>
    },
    onSuccess: (data) => {
      setSessionId(data.session_id)
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content: data.message,
          timestamp: new Date(data.timestamp),
          actions: data.actions_taken,
        },
      ])
      refetchConversations()
      // Refetch directives since new ones may have been extracted
      refetchDirectives()
    },
    onError: (error) => {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant',
          content: `Sorry, I encountered an error: ${error.message}. Please try again.`,
          timestamp: new Date(),
        },
      ])
    },
  })

  // Delete conversation mutation
  const deleteConversation = useMutation({
    mutationFn: (sid: string) => api.delete(`/chat/history/${sid}`),
    onSuccess: (_, sid) => {
      refetchConversations()
      setDeleteConfirm(null)
      if (sessionId === sid) {
        handleNewConversation()
      }
    },
  })

  // Delete directive mutation
  const deleteDirective = useMutation({
    mutationFn: (id: string) => api.delete(`/chat/directives/${id}`),
    onSuccess: () => {
      refetchDirectives()
    },
  })

  // Add memory manually
  const [newMemory, setNewMemory] = useState('')
  const addMemory = useMutation({
    mutationFn: (directive: string) =>
      api.post('/chat/directives', { directive }),
    onSuccess: () => {
      setNewMemory('')
      refetchDirectives()
    },
  })

  // Load a conversation from history
  const loadConversation = useCallback(
    async (sid: string) => {
      try {
        const history = await api.get<ConversationHistoryItem[]>('/chat/history', {
          session_id: sid,
          limit: 100,
        })
        if (history.length > 0) {
          const loadedMessages: ChatMessage[] = []
          // Sort by created_at ascending
          const sorted = [...history].sort(
            (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
          )
          for (const conv of sorted) {
            loadedMessages.push({
              id: `user-${conv.id}`,
              role: 'user',
              content: conv.user_message,
              timestamp: new Date(conv.created_at),
            })
            loadedMessages.push({
              id: `assistant-${conv.id}`,
              role: 'assistant',
              content: conv.assistant_response,
              timestamp: new Date(conv.created_at),
            })
          }
          setMessages(loadedMessages)
          setSessionId(sid)
        }
      } catch (err) {
        console.error('Failed to load conversation', err)
      }
    },
    [],
  )

  const handleSend = useCallback(() => {
    if (!input.trim() || sendMessage.isPending) return

    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: new Date(),
    }

    setMessages((prev) => [...prev, userMessage])
    sendMessage.mutate(input)
    setInput('')
  }, [input, sendMessage])

  const handleNewConversation = useCallback(() => {
    setSessionId(null)
    setMessages([
      {
        id: '1',
        role: 'assistant',
        content:
          "Hello! I'm ClimateIQ, your intelligent HVAC assistant. How can I help you today?",
        timestamp: new Date(),
      },
    ])
  }, [])

  const handleSuggestionClick = useCallback((suggestion: string) => {
    setInput(suggestion)
  }, [])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSend()
      }
    },
    [handleSend],
  )

  return (
    <div className="relative flex h-[calc(100vh-7rem)] gap-0 sm:gap-4">
      {/* Conversation Sidebar - overlay on mobile, inline on desktop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-background/80 backdrop-blur-sm lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <div
        className={`flex flex-col border-r border-border/40 bg-card dark:border-[rgba(148,163,184,0.12)] dark:bg-[rgba(10,12,16,0.78)] dark:backdrop-blur-xl transition-all ${
          sidebarOpen
            ? 'fixed inset-y-0 left-0 z-40 w-72 lg:static lg:z-auto'
            : 'w-0 overflow-hidden'
        }`}
      >
        <div className="flex items-center justify-between border-b border-border/40 p-3 dark:border-[rgba(148,163,184,0.12)]">
          <h3 className="text-sm font-bold">Conversations</h3>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleNewConversation}>
            <MessageSquarePlus className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {conversationSessions.length > 0 ? (
            <div className="space-y-1 p-2">
              {conversationSessions.map((session) => (
                <div
                  key={session.session_id}
                  className={`group flex items-center justify-between rounded-lg p-2 text-sm transition-colors cursor-pointer ${
                    sessionId === session.session_id
                      ? 'bg-primary/10 text-primary dark:bg-primary/15'
                      : 'hover:bg-muted/50 dark:hover:bg-white/5'
                  }`}
                  onClick={() => loadConversation(session.session_id)}
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{session.first_message}</p>
                    <p className="text-xs text-muted-foreground">
                      {new Date(session.created_at).toLocaleDateString()} - {session.count} messages
                    </p>
                  </div>
                  {deleteConfirm === session.session_id ? (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-red-500"
                        onClick={(e) => {
                          e.stopPropagation()
                          deleteConversation.mutate(session.session_id)
                        }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteConfirm(null)
                        }}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  ) : (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-0 group-hover:opacity-100"
                      onClick={(e) => {
                        e.stopPropagation()
                        setDeleteConfirm(session.session_id)
                      }}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="p-4 text-center text-sm text-muted-foreground">
              No conversations yet
            </div>
          )}

          {/* Memories Section */}
          <div className="border-t border-border/40 dark:border-[rgba(148,163,184,0.12)]">
            <div className="flex items-center gap-2 p-3">
              <Brain className="h-4 w-4 text-primary" />
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-bold">Memories</h3>
                <p className="text-[10px] text-muted-foreground/70 leading-tight">
                  Learned from conversations · used by the AI advisor
                </p>
              </div>
              {directives && directives.length > 0 && (
                <span className="text-xs text-muted-foreground shrink-0">{directives.length}</span>
              )}
            </div>
            {directives && directives.length > 0 && (
              <div className="space-y-1 px-2 pb-2">
                {directives.map((d) => {
                  const categoryColors: Record<string, string> = {
                    preference: 'text-blue-500',
                    comfort: 'text-blue-500',
                    constraint: 'text-blue-600',
                    routine: 'text-green-500',
                    schedule_hint: 'text-green-500',
                    occupancy: 'text-green-600',
                    house_info: 'text-amber-500',
                    energy: 'text-purple-500',
                  }
                  const color = categoryColors[d.category] ?? 'text-muted-foreground/60'
                  return (
                    <div
                      key={d.id}
                      className="group flex items-start gap-2 rounded-lg p-2 text-xs hover:bg-muted/50 dark:hover:bg-white/5"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="text-muted-foreground">{d.directive}</p>
                        <p className={`mt-0.5 text-[10px] ${color}`}>
                          {d.category.replace('_', ' ')} · {new Date(d.created_at).toLocaleDateString()}
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-5 w-5 shrink-0 opacity-0 group-hover:opacity-100"
                        onClick={() => deleteDirective.mutate(d.id)}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  )
                })}
              </div>
            )}
            {/* Add memory form */}
            <div className="px-2 pb-3 flex gap-1.5">
              <Input
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                placeholder="Add a memory…"
                className="h-7 text-xs"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newMemory.trim()) {
                    addMemory.mutate(newMemory.trim())
                  }
                }}
              />
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 shrink-0"
                disabled={!newMemory.trim() || addMemory.isPending}
                onClick={() => newMemory.trim() && addMemory.mutate(newMemory.trim())}
              >
                <Send className="h-3 w-3" />
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex flex-1 flex-col space-y-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 shrink-0"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              {sidebarOpen ? (
                <ChevronLeft className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </Button>
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">Assistant</p>
              <h2 className="flex items-center gap-2 truncate text-lg font-black tracking-tight sm:text-2xl">
                <Sparkles className="h-5 w-5 shrink-0 text-primary sm:h-6 sm:w-6 dark:drop-shadow-[0_0_6px_rgba(56,189,248,0.4)]" />
                <span className="truncate">ClimateIQ Advisor</span>
              </h2>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            <Button variant="outline" size="sm" onClick={handleNewConversation}>
              <MessageSquarePlus className="h-4 w-4 sm:mr-2" />
              <span className="hidden sm:inline">New Chat</span>
            </Button>
            <div className="hidden items-center gap-2 text-sm text-muted-foreground sm:flex">
              <div className="h-2 w-2 rounded-full bg-green-500" />
              Online
            </div>
          </div>
        </div>

        <Card className="flex flex-1 flex-col overflow-hidden">
          <CardHeader className="border-b border-border/40 py-3 dark:border-[rgba(148,163,184,0.12)]">
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <MessageCircle className="h-4 w-4" />
              {sessionId ? 'Conversation' : 'New Conversation'}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex-1 overflow-y-auto p-4">
            <div className="space-y-4">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {message.role === 'assistant' && (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 dark:shadow-[0_0_10px_rgba(56,189,248,0.15)]">
                      <Bot className="h-4 w-4 text-primary" />
                    </div>
                  )}
                  <div
                    className={`max-w-[80%] rounded-2xl px-4 py-2 ${
                      message.role === 'user'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted dark:bg-[rgba(2,6,23,0.45)] dark:backdrop-blur-[10px]'
                    }`}
                  >
                    <p className="whitespace-pre-wrap text-sm">{message.content}</p>
                    {message.actions && message.actions.length > 0 && (
                      <div className="mt-2 space-y-1 border-t border-border/30 pt-2">
                        <p className="text-xs font-medium opacity-70">Actions taken:</p>
                        {message.actions.map((action: ChatAction, i: number) => (
                          <div key={i} className="text-xs opacity-70">
                            {action.tool === 'save_memory' ? (
                              <span>
                                <span className="font-medium">Saved memory: </span>
                                {String(action.directive ?? '')}
                                {(action.category as string | undefined) && (
                                  <span className="ml-1 opacity-60">
                                    [{(action.category as string).replace(/_/g, ' ')}]
                                  </span>
                                )}
                                {action.saved === false && (action.note as string | undefined) && (
                                  <span className="ml-1 italic">{action.note as string}</span>
                                )}
                              </span>
                            ) : (
                              <>
                                {action.tool ?? 'tool'}
                                {action.error && <span className="ml-1 text-red-400">({action.error})</span>}
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    <p className="mt-1 text-xs opacity-50">
                      {message.timestamp.toLocaleTimeString()}
                    </p>
                  </div>
                  {message.role === 'user' && (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary">
                      <User className="h-4 w-4 text-primary-foreground" />
                    </div>
                  )}
                </div>
              ))}
              {sendMessage.isPending && (
                <div className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 dark:shadow-[0_0_10px_rgba(56,189,248,0.15)]">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                  <div className="rounded-2xl bg-muted px-4 py-2">
                    <div className="flex items-center gap-2">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span className="text-sm">Thinking...</span>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          </CardContent>

          {/* Suggestions */}
          {messages.length <= 2 && (
            <div className="border-t border-border/40 px-4 py-2 dark:border-[rgba(148,163,184,0.12)]">
              <div className="flex flex-wrap gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => handleSuggestionClick(suggestion)}
                    className="rounded-full border border-border/40 bg-background px-3 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground dark:border-[rgba(148,163,184,0.18)] dark:bg-[rgba(2,6,23,0.30)] dark:backdrop-blur-[10px]"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Input */}
          <div className="border-t border-border/40 p-4 dark:border-[rgba(148,163,184,0.12)]">
            <div className="flex gap-3">
              <Input
                placeholder="Ask ClimateIQ anything..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={sendMessage.isPending}
                className="flex-1"
              />
              <Button
                onClick={handleSend}
                disabled={!input.trim() || sendMessage.isPending}
                className="gap-2"
              >
                {sendMessage.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">Send</span>
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}
