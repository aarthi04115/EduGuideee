import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { createLowlight } from 'lowlight'
import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import {
  AlertCircle,
  ArrowUp,
  BookOpen,
  Check,
  Copy,
  FileText,
  Info,
  LoaderCircle,
  LogOut,
  Menu,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  UserRound,
  X,
} from 'lucide-react'
import {
  checkBackendHealth,
  attachDocumentToConversation,
  createConversation as createServerConversation,
  checkDatabaseHealth,
  deleteConversation as deleteServerConversation,
  deleteStudyMaterial,
  detachDocumentFromConversation,
  getConversationMessages,
  getConversationDocuments,
  getConversations,
  getCurrentUser,
  getCsrfToken,
  listStudyMaterials,
  logoutStudent,
  renameConversation as renameServerConversation,
  sendChatMessage,
  uploadStudyMaterial,
} from './services/api.js'
import AuthScreen from './components/AuthScreen.jsx'
import './App.css'
import 'highlight.js/styles/github.css'

const SUGGESTIONS = [
  {
    icon: '✳',
    title: 'Explain a difficult concept',
    prompt: 'Explain a difficult academic concept in simple terms, with an example.',
  },
  {
    icon: '▤',
    title: 'Summarize my lecture notes',
    prompt: 'Summarize the key ideas from my selected study materials.',
  },
  {
    icon: '◎',
    title: 'Help me prepare for exams',
    prompt: 'Help me prepare for my next exam with a focused revision plan.',
  },
  {
    icon: '</>',
    title: 'Explain code step by step',
    prompt: 'Explain this code step by step and point out the important ideas.',
  },
]

const MAX_FILE_SIZE = 10 * 1024 * 1024
const EMPTY_MESSAGES = []
const lowlight = createLowlight({
  bash,
  c,
  cpp,
  go,
  java,
  javascript,
  json,
  python,
  sql,
  typescript,
  xml,
})
const LANGUAGE_ALIASES = {
  js: 'javascript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  ts: 'typescript',
}
const SHORTCUT_MODIFIER = /Mac|iPhone|iPad/.test(navigator.platform)
  ? '⌘'
  : 'Ctrl'

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
}

function renderHighlightNode(node, key) {
  if (node.type === 'text') return node.value
  const className = node.properties?.className?.join(' ')
  return (
    <span className={className} key={key}>
      {node.children?.map(renderHighlightNode)}
    </span>
  )
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatMessageTime(value) {
  if (!value) return ''
  return new Intl.DateTimeFormat(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}

function mapServerConversation(conversation) {
  return {
    id: conversation.id,
    title: conversation.title,
    createdAt: conversation.created_at,
    updatedAt: conversation.updated_at,
    messages: [],
    attachments: [],
    loaded: false,
  }
}

function mapServerMessage(message) {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    createdAt: message.created_at,
    sources: message.sources ?? [],
  }
}

function mapServerDocument(document) {
  return {
    id: document.id,
    filename: document.filename,
    size: document.size,
    status: document.status,
    createdAt: document.created_at,
  }
}

function mergeServerConversations(serverConversations, currentConversations) {
  const currentById = new Map(
    currentConversations.map((conversation) => [conversation.id, conversation]),
  )
  const serverIds = new Set(serverConversations.map((conversation) => conversation.id))
  const restored = serverConversations.map((item) => {
    const existing = currentById.get(item.id)
    return {
      ...mapServerConversation(item),
      ...(existing?.loaded
        ? {
            messages: existing.messages,
            attachments: existing.attachments,
            loaded: true,
          }
        : {}),
    }
  })
  return [
    ...restored,
    ...currentConversations.filter((item) => !serverIds.has(item.id)),
  ].sort(
    (left, right) => new Date(right.updatedAt) - new Date(left.updatedAt),
  )
}

function App() {
  const [authState, setAuthState] = useState('loading')
  const [currentUser, setCurrentUser] = useState(null)
  const [authError, setAuthError] = useState('')
  const [databaseStatus, setDatabaseStatus] = useState('checking')
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [draft, setDraft] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [backendStatus, setBackendStatus] = useState('checking')
  const [documents, setDocuments] = useState([])
  const [selectedDocumentIds, setSelectedDocumentIds] = useState([])
  const [uploadingName, setUploadingName] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [documentsError, setDocumentsError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [historyBusy, setHistoryBusy] = useState(false)
  const [historyRetry, setHistoryRetry] = useState(0)
  const [creatingConversation, setCreatingConversation] = useState(false)
  const [loadingConversationId, setLoadingConversationId] = useState(null)
  const [busyMessageId, setBusyMessageId] = useState(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [copyMessageId, setCopyMessageId] = useState('')
  const [copyErrorId, setCopyErrorId] = useState('')
  const messageListRef = useRef(null)
  const inputRef = useRef(null)
  const fileInputRef = useRef(null)
  const activeIdRef = useRef(activeId)

  useEffect(() => {
    activeIdRef.current = activeId
  }, [activeId])

  function noteServiceFailure(error) {
    if (error.status === 503) setDatabaseStatus('offline')
    if (error.status === 0 && !error.message?.includes('timed out')) {
      setBackendStatus('offline')
    }
  }

  function requireSignIn(error) {
    setCurrentUser(null)
    setAuthError(
      error.status === 401 && error.message.toLowerCase().includes('expired')
        ? 'Your session has expired. Sign in again.'
        : '',
    )
    setAuthState('unauthenticated')
  }

  const activeConversation =
    conversations.find((conversation) => conversation.id === activeId)
  const messages = activeConversation?.messages ?? EMPTY_MESSAGES
  const attachments = activeConversation?.attachments ?? EMPTY_MESSAGES
  const hasMessages = messages.length > 0
  const hasConversationContent = hasMessages || attachments.length > 0
  const isBusy = busyMessageId !== null
  const isOpeningConversation =
    Boolean(activeId) &&
    loadingConversationId === activeId &&
    !activeConversation?.loaded

  const filteredConversations = useMemo(() => {
    const query = searchTerm.trim().toLowerCase()
    const sorted = [...conversations]
    if (!query) return sorted
    return sorted.filter((conversation) =>
      conversation.title.toLowerCase().includes(query),
    )
  }, [conversations, searchTerm])

  const [bootstrapRetry, setBootstrapRetry] = useState(0)

  useEffect(() => {
    let cancelled = false
    let retryTimer = null

    async function runBootstrap(attemptsLeft = 2) {
      try {
        await checkBackendHealth()
        let databaseError = null
        try {
          await checkDatabaseHealth()
        } catch (error) {
          databaseError = error
        }
        await getCsrfToken()
        let user = null
        let sessionError = ''
        try {
          user = await getCurrentUser()
        } catch (error) {
          if (error.status === 401) {
            sessionError = error.message.toLowerCase().includes('expired')
              ? 'Your session has expired. Sign in again.'
              : ''
          } else {
            throw error
          }
        }
        if (cancelled) return
        setBackendStatus('online')
        setDatabaseStatus(databaseError ? 'offline' : 'online')
        setAuthError(sessionError)
        if (user) {
          setCurrentUser(user)
          setAuthState('authenticated')
        } else {
          setAuthState('unauthenticated')
        }
      } catch (error) {
        if (cancelled) return
        if (attemptsLeft > 0 && (error.status === 0 || error.status === 502 || error.status === 504)) {
          setBackendStatus('checking')
          retryTimer = setTimeout(() => {
            if (!cancelled) void runBootstrap(attemptsLeft - 1)
          }, 3000)
          return
        }
        if (error.status === 401) {
          setBackendStatus('online')
          setDatabaseStatus('online')
          setAuthState('unauthenticated')
        } else if (error.status === 503) {
          setBackendStatus('online')
          setDatabaseStatus('offline')
          setAuthError(error.message)
          setAuthState('unauthenticated')
        } else {
          setBackendStatus('offline')
          setDatabaseStatus('checking')
          setAuthError(error.message)
          setAuthState('unauthenticated')
        }
      }
    }

    void runBootstrap(2)
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [bootstrapRetry])

  useEffect(() => {
    let cancelled = false

    async function checkStatus() {
      try {
        await checkBackendHealth()
        if (cancelled) return
        setBackendStatus('online')
      } catch (err) {
        if (cancelled) return
        if (err.status === 0 || err.status >= 500) {
          setBackendStatus('offline')
        }
      }

      try {
        const readyData = await checkDatabaseHealth()
        if (cancelled) return
        if (readyData && readyData.database === 'available') {
          setDatabaseStatus('online')
        } else {
          setDatabaseStatus('offline')
        }
      } catch {
        if (cancelled) return
        setDatabaseStatus('offline')
      }
    }

    const intervalMs =
      backendStatus === 'offline' || databaseStatus === 'offline' ? 5000 : 30000
    const pollTimer = setInterval(() => {
      void checkStatus()
    }, intervalMs)

    return () => {
      cancelled = true
      clearInterval(pollTimer)
    }
  }, [backendStatus, databaseStatus])

  useEffect(() => {
    if (authState !== 'authenticated') return undefined
    let cancelled = false
    async function loadAccountData() {
      setHistoryBusy(true)
      setHistoryError('')
      try {
        const [historyResult, materialsResult] = await Promise.allSettled([
          getConversations(),
          listStudyMaterials(),
        ])
        if (cancelled) return
        if (historyResult.status === 'fulfilled') {
          setConversations((current) =>
            mergeServerConversations(historyResult.value, current),
          )
        } else {
          const error = historyResult.reason
          if (error.status === 401) {
            requireSignIn(error)
          } else {
            noteServiceFailure(error)
            setHistoryError(error.message)
          }
        }
        if (materialsResult.status === 'fulfilled') {
          setDocuments(materialsResult.value.documents ?? [])
        } else {
          const error = materialsResult.reason
          if (error.status === 401) {
            requireSignIn(error)
          } else {
            noteServiceFailure(error)
            setDocumentsError(error.message)
          }
        }
      }
      finally {
        if (!cancelled) setHistoryBusy(false)
      }
    }
    void loadAccountData()
    return () => {
      cancelled = true
    }
  }, [authState, historyRetry])

  useEffect(() => {
    if (!activeId) {
      setSelectedDocumentIds([])
      return
    }
    const current = conversations.find((c) => c.id === activeId)
    if (current?.loaded && current.attachments) {
      setSelectedDocumentIds(
        current.attachments
          .filter((doc) => doc.status === 'indexed' || doc.status === 'ready')
          .map((doc) => doc.id),
      )
    }
  }, [activeId, conversations])

  useEffect(() => {
    if (hasMessages && messageListRef.current) {
      messageListRef.current.scrollTop = messageListRef.current.scrollHeight
    }
  }, [hasMessages, messages, busyMessageId])

  function updateConversation(conversationId, update) {
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === conversationId
          ? update(conversation)
          : conversation,
      ),
    )
  }

  async function refreshConversationList() {
    const result = await getConversations()
    setConversations((current) => mergeServerConversations(result, current))
  }

  async function handleLogout() {
    try {
      await logoutStudent()
      setCurrentUser(null)
      setConversations([])
      setDocuments([])
      setSelectedDocumentIds([])
      setActiveId(null)
      setAuthError('')
      setHistoryError('')
      setAuthState('unauthenticated')
    } catch (error) {
      noteServiceFailure(error)
      if (error.status === 401) {
        setCurrentUser(null)
        setConversations([])
        setDocuments([])
        setSelectedDocumentIds([])
        setActiveId(null)
        setAuthState('unauthenticated')
      } else {
        setHistoryError(error.message)
      }
    }
  }

  function handleAuthenticated(user) {
    setAuthError('')
    setCurrentUser(user)
    setConversations([])
    setActiveId(null)
    setAuthState('authenticated')
  }

  const startNewConversation = useCallback(async () => {
    if (creatingConversation) return null
    setCreatingConversation(true)
    setHistoryError('')
    try {
      const conversation = mapServerConversation(await createServerConversation())
      setConversations((current) => [conversation, ...current])
      setActiveId(conversation.id)
      setSelectedDocumentIds([])
      setSidebarOpen(false)
      inputRef.current?.focus()
      return conversation
    } catch (error) {
      if (error.status === 401) {
        requireSignIn(error)
      } else {
        noteServiceFailure(error)
        setHistoryError(error.message)
      }
      return null
    } finally {
      setCreatingConversation(false)
    }
  }, [creatingConversation])

  useEffect(() => {
    function handleShortcut(event) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        void startNewConversation()
      }
    }
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [startNewConversation])

  async function loadMessages(conversationId, force = false) {
    const current = conversations.find((conversation) => conversation.id === conversationId)
    if (!force && current?.loaded) return
    setLoadingConversationId(conversationId)
    setHistoryError('')
    try {
      const [messageResult, documentResult] = await Promise.allSettled([
        getConversationMessages(conversationId),
        getConversationDocuments(conversationId),
      ])
      if (messageResult.status === 'rejected') throw messageResult.reason
      updateConversation(conversationId, (conversation) => ({
        ...conversation,
        messages: messageResult.value.map(mapServerMessage),
        attachments:
          documentResult.status === 'fulfilled'
            ? documentResult.value.map(mapServerDocument)
            : conversation.attachments,
        loaded: true,
      }))
      if (documentResult.status === 'fulfilled') {
        if (activeIdRef.current === conversationId) {
          const restoredDocuments = documentResult.value.map(mapServerDocument)
          setSelectedDocumentIds(
            restoredDocuments
              .filter((document) => document.status === 'indexed' || document.status === 'ready')
              .map((document) => document.id),
          )
        }
      } else if (documentResult.status === 'rejected') {
        setDocumentsError(
          documentResult.reason?.message || 'Failed to load conversation documents.',
        )
      }
    } catch (error) {
      if (error.status === 401) {
        requireSignIn(error)
      } else {
        noteServiceFailure(error)
        setHistoryError(error.message)
      }
    } finally {
      setLoadingConversationId((current) =>
        current === conversationId ? null : current,
      )
    }
  }

  function selectConversation(conversationId) {
    const conversation = conversations.find((item) => item.id === conversationId)
    setActiveId(conversationId)
    setSelectedDocumentIds(
      conversation?.loaded
        ? conversation.attachments
            .filter((document) => document.status === 'indexed')
            .map((document) => document.id)
        : [],
    )
    setSidebarOpen(false)
    void loadMessages(conversationId)
  }

  async function renameConversation(conversation) {
    const title = window.prompt('Rename conversation', conversation.title)
    if (title === null) return
    const trimmedTitle = title.trim()
    if (!trimmedTitle) return
    setHistoryError('')
    try {
      const updated = await renameServerConversation(conversation.id, trimmedTitle)
      updateConversation(conversation.id, (current) => ({
        ...current,
        title: updated.title,
        updatedAt: updated.updated_at,
      }))
      await refreshConversationList()
    } catch (error) {
      if (error.status === 401) {
        requireSignIn(error)
      } else {
        noteServiceFailure(error)
        setHistoryError(error.message)
      }
    }
  }

  async function removeConversation(conversationId) {
    if (!window.confirm('Delete this conversation and its saved messages?')) return
    setHistoryError('')
    try {
      await deleteServerConversation(conversationId)
      const remaining = conversations.filter(
        (conversation) => conversation.id !== conversationId,
      )
      setConversations(remaining)
      if (activeId === conversationId) {
        if (remaining.length) {
          setActiveId(remaining[0].id)
          setSelectedDocumentIds([])
          void loadMessages(remaining[0].id)
        } else {
          setActiveId(null)
          void startNewConversation()
        }
      }
    } catch (error) {
      if (error.status === 401) {
        requireSignIn(error)
      } else {
        noteServiceFailure(error)
        setHistoryError(error.message)
      }
    }
  }

  function appendUserQuestion(conversationId, question, documentIds) {
    const userMessage = {
      id: makeId(),
      role: 'user',
      content: question,
      createdAt: new Date().toISOString(),
    }
    const assistantMessage = {
      id: makeId(),
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
      pending: true,
      documentIds,
      question,
    }
    updateConversation(conversationId, (conversation) => {
      const nextMessages = [...conversation.messages, userMessage, assistantMessage]
      return {
        ...conversation,
        loaded: true,
        title: conversation.messages.length === 0
          ? question.slice(0, 80)
          : conversation.title,
        updatedAt: new Date().toISOString(),
        messages: nextMessages,
      }
    })
    return assistantMessage.id
  }

  async function requestAnswer(conversationId, messageId, question, documentIds) {
    setBusyMessageId(messageId)
    try {
      const response = await sendChatMessage(
        question,
        conversationId,
        documentIds,
      )
      setBackendStatus('online')
      updateConversation(conversationId, (conversation) => ({
        ...conversation,
        id: response.conversation_id,
        updatedAt: new Date().toISOString(),
        messages: conversation.messages.map((message) =>
          message.id === messageId
            ? {
                ...message,
                content: response.answer || 'EduGuide returned an empty answer.',
                sources: response.sources ?? [],
                pending: false,
                isError: false,
              }
            : message,
        ),
      }))
      setActiveId(response.conversation_id)
      await loadMessages(response.conversation_id, true)
      try {
        await refreshConversationList()
      } catch (error) {
        if (error.status === 401) {
          requireSignIn(error)
        } else {
          noteServiceFailure(error)
          setHistoryError(error.message)
        }
      }
    } catch (error) {
      if (error.status === 401) {
        requireSignIn(error)
        return
      }
      if (error.status === 0) {
        setBackendStatus('offline')
      } else if (error.status === 503) {
        setDatabaseStatus('offline')
      }

      let displayMessage = error.message
      if (error.status === 403) {
        displayMessage = 'Action forbidden or session expired. Please refresh the page and try again.'
      } else if (error.status === 429) {
        displayMessage = error.message || 'AI provider rate limit reached. Please wait a moment before trying again.'
      } else if (error.status === 504) {
        displayMessage = 'The AI provider timed out generating a response. Please try again.'
      }

      updateConversation(conversationId, (conversation) => ({
        ...conversation,
        messages: conversation.messages.map((message) =>
          message.id === messageId
            ? {
                ...message,
                content: displayMessage,
                pending: false,
                isError: true,
                question,
              }
            : message,
        ),
      }))
      if (error.status === 502) {
        try {
          await refreshConversationList()
        } catch (refreshError) {
          if (refreshError.status === 401) {
            requireSignIn(refreshError)
          } else {
            noteServiceFailure(refreshError)
            setHistoryError(refreshError.message)
          }
        }
      }
    } finally {
      setBusyMessageId(null)
    }
  }

  async function sendMessage(question = draft) {
    const content = question.trim()
    if (!content || isBusy || creatingConversation || isOpeningConversation) return
    const conversation = activeConversation ?? await startNewConversation()
    if (!conversation) return
    setDraft('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
    const documentIds = [...selectedDocumentIds]
    const assistantMessageId = appendUserQuestion(
      conversation.id,
      content,
      documentIds,
    )
    void requestAnswer(
      conversation.id,
      assistantMessageId,
      content,
      documentIds,
    )
  }

  function retryMessage(message) {
    if (isBusy) return
    const conversationId = activeConversation.id
    updateConversation(conversationId, (conversation) => ({
      ...conversation,
      messages: conversation.messages.map((item) =>
        item.id === message.id
          ? { ...item, content: '', pending: true, isError: false }
          : item,
      ),
    }))
    void requestAnswer(
      conversationId,
      message.id,
      message.question,
      message.documentIds ?? [],
    )
  }

  function handleInputKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendMessage()
    }
  }

  async function handleSuggestion(prompt) {
    setDraft(prompt)
    inputRef.current?.focus()
  }

  async function copyAnswer(message) {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopyMessageId(message.id)
      setCopyErrorId('')
      window.setTimeout(() => setCopyMessageId(''), 1800)
    } catch {
      setCopyErrorId(message.id)
    }
  }

  async function toggleDocument(document) {
    if (isOpeningConversation) return
    const alreadySelected = selectedDocumentIds.includes(document.id)
    if (alreadySelected) {
      setSelectedDocumentIds((current) =>
        current.filter((id) => id !== document.id),
      )
      return
    }
    const conversation = activeConversation ?? await startNewConversation()
    if (!conversation) return
    try {
      const attached = await attachDocumentToConversation(
        conversation.id,
        document.id,
      )
      const mapped = mapServerDocument(attached)
      updateConversation(conversation.id, (current) => ({
        ...current,
        attachments: current.attachments.some((item) => item.id === mapped.id)
          ? current.attachments
          : [...current.attachments, mapped],
      }))
      setSelectedDocumentIds((current) =>
        current.includes(mapped.id) ? current : [...current, mapped.id],
      )
    } catch (error) {
      if (error.status === 401) {
        requireSignIn(error)
      } else {
        noteServiceFailure(error)
        setDocumentsError(error.message)
      }
    }
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList ?? [])
    if (!files.length || uploadingName || isOpeningConversation) return
    setUploadError('')
    const validFiles = []
    const rejectedFiles = []
    for (const file of files) {
      const extension = file.name.split('.').pop()?.toLowerCase()
      if (!['pdf', 'txt'].includes(extension)) {
        rejectedFiles.push(`${file.name}: only PDF and TXT files are supported.`)
      } else if (file.size === 0) {
        rejectedFiles.push(`${file.name}: the selected file is empty.`)
      } else if (file.size > MAX_FILE_SIZE) {
        rejectedFiles.push(`${file.name}: the maximum file size is 10 MB.`)
      } else {
        validFiles.push(file)
      }
    }
    if (rejectedFiles.length) setUploadError(rejectedFiles.join(' '))
    if (!validFiles.length) {
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    let conversation = activeConversation ?? await startNewConversation()
    if (!conversation) {
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    for (const file of validFiles) {
      const optimisticAttachment = {
        id: makeId(),
        filename: file.name,
        size: file.size,
        status: 'selected',
        createdAt: new Date().toISOString(),
        temporary: true,
      }
      updateConversation(conversation.id, (current) => ({
        ...current,
        attachments: [...current.attachments, optimisticAttachment],
      }))
      await new Promise((resolve) => window.requestAnimationFrame(resolve))
      setUploadingName(file.name)
      updateConversation(conversation.id, (current) => ({
        ...current,
        attachments: current.attachments.map((attachment) =>
          attachment.id === optimisticAttachment.id
            ? { ...attachment, status: 'uploading' }
            : attachment,
        ),
      }))
      const processingTimer = window.setTimeout(() => {
        updateConversation(conversation.id, (current) => ({
          ...current,
          attachments: current.attachments.map((attachment) =>
            attachment.id === optimisticAttachment.id
              ? { ...attachment, status: 'processing' }
              : attachment,
          ),
        }))
      }, 250)
      try {
        const document = await uploadStudyMaterial(file, conversation.id)
        window.clearTimeout(processingTimer)
        setBackendStatus('online')
        setDocuments((current) => [
          document,
          ...current.filter((item) => item.id !== document.id),
        ])
        setSelectedDocumentIds((current) =>
          current.includes(document.id) ? current : [...current, document.id],
        )
        const uploadedConversation = conversations.find(
          (item) => item.id === document.conversation_id,
        )
        if (!uploadedConversation) {
          conversation = mapServerConversation({
            id: document.conversation_id,
            title: `Study material: ${file.name}`.slice(0, 120),
            created_at: document.created_at,
            updated_at: document.created_at,
          })
          setConversations((current) => [
            conversation,
            ...current.filter((item) => item.id !== conversation.id),
          ])
        }
        setActiveId(document.conversation_id)
        const mappedDocument = mapServerDocument(document)
        updateConversation(document.conversation_id, (current) => ({
          ...current,
          attachments: [
            ...current.attachments.filter(
              (attachment) => attachment.id !== optimisticAttachment.id,
            ),
            mappedDocument,
          ],
          loaded: true,
        }))
        conversation = conversations.find(
          (item) => item.id === document.conversation_id,
        ) ?? {
          id: document.conversation_id,
          title: `Study material: ${file.name}`.slice(0, 120),
        }
        try {
          await refreshConversationList()
        } catch (refreshError) {
          if (refreshError.status === 401) {
            requireSignIn(refreshError)
          } else {
            noteServiceFailure(refreshError)
            setHistoryError(refreshError.message)
          }
        }
      } catch (error) {
        window.clearTimeout(processingTimer)
        updateConversation(conversation.id, (current) => ({
          ...current,
          attachments: current.attachments.map((attachment) =>
            attachment.id === optimisticAttachment.id
              ? {
                  ...attachment,
                  status: 'failed',
                  error: error.message,
                  temporary: true,
                }
              : attachment,
          ),
        }))
        setUploadError(`${file.name}: ${error.message}`)
      } finally {
        setUploadingName('')
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  async function removeDocument(document) {
    if (!window.confirm(`Remove "${document.filename}" and its indexed copy?`)) {
      return
    }
    setDocumentsError('')
    try {
      await deleteStudyMaterial(document.id)
      setDocuments((current) =>
        current.filter((item) => item.id !== document.id),
      )
      setSelectedDocumentIds((current) =>
        current.filter((id) => id !== document.id),
      )
      setConversations((current) =>
        current.map((conversation) => ({
          ...conversation,
          attachments: conversation.attachments.filter(
            (attachment) => attachment.id !== document.id,
          ),
        })),
      )
    } catch (error) {
      setDocumentsError(error.message)
    }
  }

  function removeAttachment(attachment) {
    if (attachment.temporary) {
      if (activeId) {
        updateConversation(activeId, (conversation) => ({
          ...conversation,
          attachments: conversation.attachments.filter(
            (item) => item.id !== attachment.id,
          ),
        }))
      }
      return
    }
    if (!activeId) return
    setSelectedDocumentIds((current) =>
      current.filter((id) => id !== attachment.id),
    )
    void detachDocumentFromConversation(activeId, attachment.id)
      .then(() => {
        updateConversation(activeId, (conversation) => ({
          ...conversation,
          attachments: conversation.attachments.filter(
            (item) => item.id !== attachment.id,
          ),
        }))
      })
      .catch((error) => {
        if (error.status === 401) {
          requireSignIn(error)
        } else {
          noteServiceFailure(error)
          setDocumentsError(error.message)
          setSelectedDocumentIds((current) =>
            current.includes(attachment.id)
              ? current
              : [...current, attachment.id],
          )
        }
      })
  }

  const connectedLabel =
    backendStatus === 'online'
      ? 'Backend connected'
      : backendStatus === 'checking'
        ? 'Connecting'
        : 'Backend unavailable'
  const databaseLabel =
    databaseStatus === 'online'
      ? 'Database ready'
      : databaseStatus === 'checking'
        ? 'Database checking'
        : 'Database unavailable'

  if (authState === 'loading') {
    return (
      <main className="auth-loading">
        <LoaderCircle size={23} className="auth-spinner" />
        <span>Checking your EduGuide session…</span>
      </main>
    )
  }

  if (authState === 'unauthenticated') {
    return (
      <AuthScreen
        onAuthenticated={handleAuthenticated}
        backendStatus={backendStatus}
        initialError={authError}
        onRetry={() => setBootstrapRetry((v) => v + 1)}
        onRequestError={(error) => {
          if (error.status === 503) setDatabaseStatus('offline')
          if (error.status === 0 && !error.message?.includes('timed out')) {
            setBackendStatus('offline')
          }
        }}
      />
    )
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      {sidebarOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <BookOpen size={21} strokeWidth={2.1} />
          </div>
          <div className="brand-copy">
            <span className="brand-name">EduGuide</span>
            <span className="brand-subtitle">Sri Sairam Engineering College</span>
          </div>
          <button
            className="icon-button sidebar-close"
            aria-label="Close navigation"
            onClick={() => setSidebarOpen(false)}
          >
            <X size={19} />
          </button>
        </div>

        <button
          className="new-chat-button"
          onClick={() => void startNewConversation()}
          disabled={creatingConversation}
        >
          <Plus size={18} />
          <span className="new-chat-label">New chat</span>
          <span className="new-chat-shortcut">{SHORTCUT_MODIFIER} K</span>
        </button>

        <label
          className="conversation-search"
          onClick={
            sidebarCollapsed
              ? () => setSidebarCollapsed(false)
              : undefined
          }
        >
          <Search size={16} aria-hidden="true" />
          <span className="sr-only">Search conversations</span>
          <input
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="Search conversations"
          />
          {searchTerm && (
            <button
              type="button"
              className="search-clear"
              aria-label="Clear conversation search"
              onClick={() => setSearchTerm('')}
            >
              <X size={14} />
            </button>
          )}
        </label>

        <div className="sidebar-scroll">
          <div className="sidebar-section-heading">
            <span>Recent conversations</span>
          </div>
          {historyError && (
            <div className="sidebar-error" role="alert">
              <AlertCircle size={15} />
              <span>{historyError}</span>
              <button
                className="icon-button tiny-icon"
                aria-label="Retry loading conversations"
                title="Retry loading conversations"
                onClick={() => setHistoryRetry((value) => value + 1)}
              >
                <LoaderCircle size={13} />
              </button>
            </div>
          )}
          <div className="conversation-list">
            {filteredConversations.map((conversation) => (
              <div
                className={`conversation-row ${
                  conversation.id === (activeId ?? activeConversation?.id)
                    ? 'is-active'
                    : ''
                }`}
                key={conversation.id}
              >
                <button
                  className="conversation-select"
                  onClick={() => selectConversation(conversation.id)}
                  title={conversation.title}
                >
                  <MessageCircle size={16} />
                  <span>{conversation.title}</span>
                </button>
                <div className="conversation-actions">
                  <button
                    className="icon-button tiny-icon"
                    aria-label={`Rename ${conversation.title}`}
                    title="Rename conversation"
                    onClick={() => renameConversation(conversation)}
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    className="icon-button tiny-icon delete-action"
                    aria-label={`Delete ${conversation.title}`}
                    title="Delete conversation"
                    onClick={() => removeConversation(conversation.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
            {historyBusy && conversations.length === 0 && (
              <p className="sidebar-empty">Loading your conversations…</p>
            )}
            {filteredConversations.length === 0 && !historyBusy && (
              <p className="sidebar-empty">
                {searchTerm
                  ? 'No conversations match your search.'
                  : 'Your recent conversations will appear here.'}
              </p>
            )}
          </div>

          <div className="materials-heading">
            <div className="sidebar-section-heading">
              <span>My study materials</span>
              <span className="material-count">{documents.length}</span>
            </div>
            <p className="materials-description">
              Upload notes and ask questions grounded in your material.
            </p>
          </div>

          <label
            className={`materials-dropzone ${uploadingName ? 'is-uploading' : ''}`}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault()
              void uploadFiles(event.dataTransfer.files)
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.txt,application/pdf,text/plain"
              multiple
              onChange={(event) => void uploadFiles(event.target.files)}
              disabled={Boolean(uploadingName) || isOpeningConversation}
            />
            <span className="dropzone-icon">
              {uploadingName ? (
                <LoaderCircle size={18} className="spin" />
              ) : (
                <Upload size={18} />
              )}
            </span>
            <span className="dropzone-text">
              <strong>
                {uploadingName ? 'Indexing material…' : 'Add study materials'}
              </strong>
              <small>
                {uploadingName || 'PDF or TXT · up to 10 MB'}
              </small>
            </span>
            <span className="browse-label">Browse</span>
          </label>
          <p className="privacy-note">
            <ShieldCheck size={13} />
            <span>Private to your authenticated account</span>
          </p>

          {(uploadError || documentsError) && (
            <div className="sidebar-error" role="status">
              <AlertCircle size={15} />
              <span>{uploadError || documentsError}</span>
              <button
                className="icon-button tiny-icon"
                aria-label="Dismiss file message"
                onClick={() => {
                  setUploadError('')
                  setDocumentsError('')
                }}
              >
                <X size={13} />
              </button>
            </div>
          )}

          <div className="document-list">
            {documents.map((document) => {
              const selected = selectedDocumentIds.includes(document.id)
              return (
                <div
                  className={`document-row ${selected ? 'document-selected' : ''}`}
                  key={document.id}
                >
                  <button
                    className="document-select"
                    onClick={() => void toggleDocument(document)}
                    aria-pressed={selected}
                    title={
                      selected
                        ? 'Stop using this material for new questions'
                        : 'Use this material for new questions'
                    }
                  >
                    <FileText size={16} />
                    <span className="document-label">
                      <strong>{document.filename}</strong>
                      <small>
                        {document.status === 'indexed' ? (
                          <Check size={11} />
                        ) : document.status === 'failed' ? (
                          <AlertCircle size={11} />
                        ) : (
                          <LoaderCircle size={11} className="spin" />
                        )}
                        {document.status === 'indexed'
                          ? 'Ready'
                          : document.status === 'failed'
                            ? 'Failed'
                            : 'Processing'}{' '}
                        · {formatBytes(document.size)}
                      </small>
                    </span>
                    <span className="document-check" aria-hidden="true">
                      {selected && <Check size={13} />}
                    </span>
                  </button>
                  <button
                    className="icon-button tiny-icon document-delete"
                    aria-label={`Remove ${document.filename}`}
                    title="Remove material"
                    onClick={() => void removeDocument(document)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              )
            })}
            {documents.length === 0 && (
              <p className="materials-empty">
                {documentsError
                  ? 'Reconnect to load your materials.'
                  : 'No materials yet. Upload a PDF or text file to begin.'}
              </p>
            )}
          </div>
        </div>

        <div className="sidebar-bottom">
          <button
            className="about-button"
            title="Help & about"
            onClick={() => setAboutOpen(true)}
          >
            <Info size={17} />
            <span>Help &amp; about</span>
          </button>
          <div className="profile-card">
            <div className="profile-avatar">
              <UserRound size={18} />
            </div>
            <div className="profile-copy">
              <strong>{currentUser?.name || 'Student account'}</strong>
              <span>{currentUser?.email || 'Signed in securely'}</span>
            </div>
            <button
              className="icon-button tiny-icon profile-logout"
              aria-label="Log out"
              title="Log out"
              onClick={() => void handleLogout()}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="topbar-leading">
            <button
              className="icon-button desktop-sidebar-toggle"
              aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            >
              {sidebarCollapsed ? (
                <PanelLeftOpen size={19} />
              ) : (
                <PanelLeftClose size={19} />
              )}
            </button>
            <button
              className="icon-button mobile-menu-button"
              aria-label="Open navigation"
              onClick={() => setSidebarOpen(true)}
            >
              <Menu size={20} />
            </button>
            <div className="mobile-brand-mark" aria-hidden="true">
              <BookOpen size={18} />
            </div>
            <div className="topbar-title">
              <strong>Academic Assistant</strong>
              <span>Your AI academic companion</span>
            </div>
          </div>
          <div className={`connection-status status-${backendStatus}`}>
            <span className="connection-dot" />
            <span>{connectedLabel}</span>
          </div>
          <div className={`connection-status status-${databaseStatus}`}>
            <span className="connection-dot" />
            <span>{databaseLabel}</span>
          </div>
        </header>

        <section className="chat-content">
          <div className="message-list" ref={messageListRef}>
            {activeId && loadingConversationId === activeId ? (
              <div className="auth-loading conversation-loading">
                <LoaderCircle size={21} className="auth-spinner" />
                <span>Restoring this conversation…</span>
              </div>
            ) : !hasConversationContent ? (
              <div className="welcome-screen">
                <div className="welcome-emblem">
                  <Sparkles size={25} />
                </div>
                <p className="eyebrow">LEARN WITH CLARITY</p>
                <h1>Welcome to EduGuide</h1>
                <p className="welcome-lead">
                  Your AI academic companion for learning, understanding, and
                  exploring concepts.
                </p>
                <p className="welcome-support">
                  Ask a question, upload your lecture notes, or explore a
                  difficult topic. EduGuide helps you learn using your study
                  materials and academic knowledge.
                </p>
                {!activeConversation && conversations.length > 0 && (
                  <p className="welcome-support">
                    Select a saved conversation or start a new chat to begin.
                  </p>
                )}
                {backendStatus === 'offline' && (
                  <div className="connection-alert" role="alert">
                    <AlertCircle size={17} />
                    <span>
                      Unable to connect to EduGuide. Start the backend at
                      localhost:8000, then try again.
                    </span>
                  </div>
                )}
                <div className="suggestion-grid">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      className="suggestion-card"
                      key={suggestion.title}
                      onClick={() => void handleSuggestion(suggestion.prompt)}
                      disabled={creatingConversation}
                    >
                      <span className="suggestion-icon" aria-hidden="true">
                        {suggestion.icon}
                      </span>
                      <span>{suggestion.title}</span>
                      <ArrowUp
                        className="suggestion-arrow"
                        size={15}
                        aria-hidden="true"
                      />
                    </button>
                  ))}
                </div>
                <div className="welcome-footnote">
                  <ShieldCheck size={14} />
                  <span>Grounded in learning, built for Sri Sairam students</span>
                </div>
              </div>
            ) : (
              <div className="messages-inner">
                {attachments.map((attachment) => {
                  const fileType = attachment.filename
                    .split('.')
                    .pop()
                    ?.toUpperCase()
                  const statusLabel =
                    attachment.status === 'indexed'
                      ? 'Ready'
                      : attachment.status === 'failed'
                        ? 'Failed'
                        : attachment.status === 'processing'
                          ? 'Processing'
                          : attachment.status === 'uploading'
                            ? 'Uploading'
                            : 'Selected'
                  return (
                    <article
                      className="message message-user attachment-message"
                      key={`attachment-${attachment.id}`}
                    >
                      <div className="message-avatar" aria-hidden="true">
                        <UserRound size={17} />
                      </div>
                      <div className="message-main">
                        <div className="message-meta">
                          <strong>You</strong>
                          <span>{formatMessageTime(attachment.createdAt)}</span>
                        </div>
                        <div className="message-bubble attachment-bubble">
                          <div
                            className={`attachment-card attachment-${attachment.status}`}
                          >
                            <FileText size={21} aria-hidden="true" />
                            <span className="attachment-file-type">
                              {fileType}
                            </span>
                            <div className="attachment-copy">
                              <strong>{attachment.filename}</strong>
                              <span>
                                {attachment.size
                                  ? `${formatBytes(attachment.size)} · `
                                  : ''}
                                {statusLabel}
                              </span>
                              {attachment.error && (
                                <span className="attachment-error">
                                  {attachment.error}
                                </span>
                              )}
                            </div>
                            {attachment.status === 'uploading' ||
                            attachment.status === 'processing' ? (
                              <LoaderCircle
                                className="spin"
                                size={17}
                                aria-label={statusLabel}
                              />
                            ) : (
                              <button
                                className="icon-button tiny-icon attachment-remove"
                                aria-label={`Remove ${attachment.filename}`}
                                title="Remove attachment"
                                onClick={() => removeAttachment(attachment)}
                              >
                                <X size={15} />
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </article>
                  )
                })}
                {messages.map((message) => (
                  <article
                    className={`message message-${message.role}`}
                    key={message.id}
                  >
                    <div className="message-avatar" aria-hidden="true">
                      {message.role === 'assistant' ? (
                        <BookOpen size={17} />
                      ) : (
                        <UserRound size={17} />
                      )}
                    </div>
                    <div className="message-main">
                      <div className="message-meta">
                        <strong>
                          {message.role === 'assistant' ? 'EduGuide' : 'You'}
                        </strong>
                        <span>{formatMessageTime(message.createdAt)}</span>
                      </div>
                      <div
                        className={`message-bubble ${
                          message.isError ? 'message-error' : ''
                        }`}
                      >
                        {message.pending ? (
                          <div className="thinking-indicator" aria-live="polite">
                            <span className="thinking-dots">
                              <i />
                              <i />
                              <i />
                            </span>
                            <span>Thinking through your question…</span>
                          </div>
                        ) : message.isError ? (
                          <div className="error-answer" role="alert">
                            <AlertCircle size={17} />
                            <span>{message.content}</span>
                            {message.question && (
                              <button
                                className="retry-button"
                                onClick={() => retryMessage(message)}
                                disabled={isBusy}
                              >
                                Try again
                              </button>
                            )}
                          </div>
                        ) : message.role === 'assistant' ? (
                          <>
                            <div className="markdown-body">
                              <ReactMarkdown
                                remarkPlugins={[remarkGfm]}
                                components={{
                                  code({ children, className, ...props }) {
                                    const languageMatch =
                                      /language-([\w-]+)/.exec(className ?? '')
                                    const requestedLanguage =
                                      languageMatch?.[1]?.toLowerCase()
                                    const language =
                                      LANGUAGE_ALIASES[requestedLanguage] ??
                                      requestedLanguage
                                    if (
                                      !language ||
                                      !lowlight.registered(language)
                                    ) {
                                      return (
                                        <code className={className} {...props}>
                                          {children}
                                        </code>
                                      )
                                    }
                                    const source = String(children).replace(
                                      /\n$/,
                                      '',
                                    )
                                    const highlighted = lowlight.highlight(
                                      language,
                                      source,
                                    )
                                    return (
                                      <code className={className} {...props}>
                                        {highlighted.children.map(
                                          renderHighlightNode,
                                        )}
                                      </code>
                                    )
                                  },
                                }}
                              >
                                {message.content}
                              </ReactMarkdown>
                            </div>
                            {message.sources?.length > 0 && (
                              <div className="source-reference">
                                <span>Sources</span>
                                <ul>
                                  {message.sources.map((source) => (
                                    <li key={source.id}>
                                      <FileText size={13} />
                                      {source.url ? (
                                        <a
                                          href={source.url}
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          {source.title || source.filename}
                                        </a>
                                      ) : (
                                        source.filename
                                      )}
                                      {source.page_number
                                        ? ` · p. ${source.page_number}`
                                        : ''}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            <div className="message-tools">
                              <button
                                className="message-tool"
                                onClick={() => void copyAnswer(message)}
                              >
                                {copyMessageId === message.id ? (
                                  <Check size={14} />
                                ) : (
                                  <Copy size={14} />
                                )}
                                <span>
                                  {copyMessageId === message.id
                                    ? 'Copied'
                                    : 'Copy'}
                                </span>
                              </button>
                              {copyErrorId === message.id && (
                                <span className="copy-error">
                                  Clipboard unavailable
                                </span>
                              )}
                            </div>
                          </>
                        ) : (
                          <p className="user-message-text">{message.content}</p>
                        )}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>

          <div className="composer-area">
            {selectedDocumentIds.length > 0 && (
              <div className="selected-material-banner">
                <FileText size={14} />
                <span>
                  Answers will use {selectedDocumentIds.length} selected{' '}
                  {selectedDocumentIds.length === 1 ? 'material' : 'materials'}
                </span>
                <button
                  onClick={() => setSelectedDocumentIds([])}
                  aria-label="Stop using selected materials"
                >
                  <X size={14} />
                </button>
              </div>
            )}
            {backendStatus === 'offline' && hasMessages && (
              <div className="composer-offline" role="status">
                <AlertCircle size={15} />
                <span>Backend unavailable · Your message cannot be sent yet.</span>
                <button
                  type="button"
                  style={{
                    marginLeft: '8px',
                    padding: '2px 8px',
                    borderRadius: '4px',
                    border: '1px solid currentColor',
                    background: 'transparent',
                    color: 'inherit',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                  onClick={async () => {
                    try {
                      await checkBackendHealth()
                      setBackendStatus('online')
                      setDatabaseStatus('online')
                    } catch {
                      // still offline
                    }
                  }}
                >
                  Retry
                </button>
              </div>
            )}
            <div className="composer">
              <button
                className="composer-attach"
                aria-label="Upload a study material"
                title="Upload a PDF or TXT study material"
                onClick={() => fileInputRef.current?.click()}
                disabled={Boolean(uploadingName) || isOpeningConversation}
              >
                <Paperclip size={19} />
              </button>
              <label className="composer-field">
                <span className="sr-only">Ask an academic question</span>
                <textarea
                  ref={inputRef}
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value)
                    event.target.style.height = 'auto'
                    event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`
                  }}
                  onKeyDown={handleInputKeyDown}
                  placeholder="Ask EduGuide anything about your studies…"
                  rows={1}
                  maxLength={4000}
                  disabled={
                    isBusy || creatingConversation || isOpeningConversation
                  }
                />
              </label>
              <button
                className={`send-button ${draft.trim() ? 'send-ready' : ''}`}
                onClick={() => sendMessage()}
                disabled={
                  !draft.trim() ||
                  isBusy ||
                  creatingConversation ||
                  isOpeningConversation
                }
                aria-label={isBusy ? 'EduGuide is responding' : 'Send message'}
                title="Send message"
              >
                {isBusy ? (
                  <LoaderCircle size={18} className="spin" />
                ) : (
                  <ArrowUp size={19} />
                )}
              </button>
            </div>
            <p className="composer-hint">
              <span>Enter to send</span>
              <span className="hint-divider">·</span>
              <span>Shift + Enter for a new line</span>
              <span className="hint-tail">
                EduGuide can make mistakes. Verify important information.
              </span>
            </p>
          </div>
        </section>
      </main>

      {aboutOpen && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setAboutOpen(false)
          }}
        >
          <section
            className="about-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="about-title"
          >
            <button
              className="icon-button modal-close"
              aria-label="Close about dialog"
              onClick={() => setAboutOpen(false)}
            >
              <X size={18} />
            </button>
            <div className="about-mark">
              <BookOpen size={23} />
            </div>
            <p className="eyebrow">YOUR AI ACADEMIC COMPANION</p>
            <h2 id="about-title">About EduGuide</h2>
            <p>
              EduGuide helps Sri Sairam Engineering College students explore
              academic topics and ask questions about their own study
              materials.
            </p>
            <div className="about-details">
              <div>
                <strong>Chat history</strong>
                <span>Saved securely to your account</span>
              </div>
              <div>
                <strong>Privacy</strong>
                <span>Your conversations and materials are account-scoped</span>
              </div>
              <div>
                <strong>Study materials</strong>
                <span>PDF and TXT · up to 10 MB per file</span>
              </div>
              <div>
                <strong>Backend</strong>
                <span>{connectedLabel}</span>
              </div>
            </div>
            <p className="about-branding">
              EduGuide — Powered by Sri Sairam Engineering College
            </p>
          </section>
        </div>
      )}

      <span className="sr-only" aria-live="polite">
        {isBusy ? 'EduGuide is generating a response.' : ''}
      </span>
    </div>
  )
}

export default App
