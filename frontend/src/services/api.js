let rawBase = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000'
if (rawBase && !rawBase.startsWith('http://') && !rawBase.startsWith('https://')) {
  rawBase = `https://${rawBase}`
}
const API_BASE_URL = rawBase.replace(/\/+$/, '')
let csrfToken

export class ApiError extends Error {
  constructor(message, { status = 0, conversationId = null, cause } = {}) {
    super(message, { cause })
    this.name = 'ApiError'
    this.status = status
    this.conversationId = conversationId
  }
}

async function request(path, options = {}, timeoutMs = 60000) {
  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      credentials: 'include',
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch (error) {
    const message =
      error.name === 'TimeoutError' || error.name === 'AbortError'
        ? 'The request timed out. Please try again.'
        : 'Unable to connect to EduGuide. Check that the backend is running and try again.'
    throw new ApiError(message, { cause: error })
  }

  const responseText = await response.text()
  let payload
  try {
    payload = responseText ? JSON.parse(responseText) : {}
  } catch (error) {
    throw new ApiError(
      'EduGuide returned an invalid response. Please try again.',
      { status: response.status, cause: error },
    )
  }

  if (!response.ok) {
    const detail = payload.detail
    const message =
      typeof detail === 'string'
        ? detail
        : typeof detail?.message === 'string'
          ? detail.message
          : `EduGuide could not complete the request (${response.status}).`
    throw new ApiError(message, {
      status: response.status,
      conversationId: payload.conversation_id ?? detail?.conversation_id ?? null,
    })
  }
  return payload
}

async function mutate(path, options = {}, timeoutMs = 60000) {
  if (!csrfToken) await getCsrfToken()
  const headers = new Headers(options.headers)
  headers.set('X-CSRF-Token', csrfToken)
  return request(path, { ...options, headers }, timeoutMs)
}

function jsonOptions(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function checkBackendHealth() {
  return request('/', {}, 5000)
}

export function checkDatabaseHealth() {
  return request('/health/ready', {}, 5000)
}

export async function getCsrfToken() {
  const result = await request('/auth/csrf', {}, 10000)
  csrfToken = result.csrf_token
  return csrfToken
}

export async function registerStudent({ name, email, password }) {
  if (!csrfToken) await getCsrfToken()
  const result = await mutate(
    '/auth/register',
    jsonOptions('POST', { name, email, password }),
  )
  await getCsrfToken()
  return result.user
}

export async function loginStudent({ email, password }) {
  if (!csrfToken) await getCsrfToken()
  const result = await mutate(
    '/auth/login',
    jsonOptions('POST', { email, password }),
  )
  await getCsrfToken()
  return result.user
}

export async function getCurrentUser() {
  const result = await request('/auth/me')
  return result.user
}

export async function logoutStudent() {
  try {
    await mutate('/auth/logout', jsonOptions('POST', {}))
  } finally {
    csrfToken = null
  }
}

export function getConversations() {
  return request('/conversations')
}

export function createConversation(title = null) {
  return mutate('/conversations', jsonOptions('POST', { title }))
}

export function getConversationMessages(conversationId) {
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
  )
}

export function getConversationDocuments(conversationId) {
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/documents`,
  )
}

export function attachDocumentToConversation(conversationId, documentId) {
  return mutate(
    `/conversations/${encodeURIComponent(conversationId)}/documents`,
    jsonOptions('POST', { document_id: documentId }),
  )
}

export function detachDocumentFromConversation(conversationId, documentId) {
  return mutate(
    `/conversations/${encodeURIComponent(conversationId)}/documents/${encodeURIComponent(documentId)}`,
    { method: 'DELETE' },
  )
}

export function renameConversation(conversationId, title) {
  return mutate(
    `/conversations/${encodeURIComponent(conversationId)}`,
    jsonOptions('PATCH', { title }),
  )
}

export function deleteConversation(conversationId) {
  return mutate(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
  )
}

export function sendChatMessage(
  question,
  conversationId = null,
  documentIds = [],
) {
  return mutate(
    '/chat',
    jsonOptions('POST', {
      question,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      document_ids: documentIds,
    }),
  )
}

export function listStudyMaterials() {
  return request('/documents', {}, 10000)
}

export function uploadStudyMaterial(file, conversationId = null) {
  const formData = new FormData()
  formData.append('file', file)
  if (conversationId) formData.append('conversation_id', conversationId)
  return mutate(
    '/documents/upload',
    {
      method: 'POST',
      body: formData,
    },
    120000,
  )
}

export function deleteStudyMaterial(documentId) {
  return mutate(`/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
  })
}
