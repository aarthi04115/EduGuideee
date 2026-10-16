import assert from 'node:assert/strict'
import test from 'node:test'

test('authenticated API calls include cookies and the CSRF token', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const api = await import('./api.js')

  try {
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      if (url.endsWith('/')) {
        return new Response(JSON.stringify({ message: 'EduGuide backend is running' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/health/ready')) {
        return new Response(JSON.stringify({ detail: 'Database schema is not initialized.' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/auth/me')) {
        return new Response(JSON.stringify({ detail: 'Authentication required.' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.includes('/conversations/conversation-1/documents/document-1')) {
        return new Response(null, { status: 204 })
      }
      if (url.endsWith('/conversations/conversation-1/documents')) {
        return new Response(
          JSON.stringify(options.method === 'POST' ? {
            id: 'document-1',
            filename: 'notes.txt',
            size: 5,
            status: 'indexed',
            created_at: '2026-10-04T00:00:00+00:00',
          } : []),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/documents/upload')) {
        return new Response(
          JSON.stringify({
            id: 'document-1',
            filename: 'notes.txt',
            size: 5,
            status: 'indexed',
            created_at: '2026-10-04T00:00:00+00:00',
            conversation_id: 'conversation-1',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify({ csrf_token: 'test-nonce.test-signature' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    await api.checkBackendHealth()
    await assert.rejects(api.checkDatabaseHealth(), { status: 503 })
    await api.getCurrentUser().catch((error) => {
      assert.equal(error.status, 401)
      assert.equal(error.message, 'Authentication required.')
    })
    await api.getCsrfToken()
    await api.createConversation()
    await api.sendChatMessage('What is RAG?', 'conversation-1', ['document-1'])
    await api.getConversationDocuments('conversation-1')
    await api.attachDocumentToConversation('conversation-1', 'document-1')
    await api.detachDocumentFromConversation('conversation-1', 'document-1')
    await api.uploadStudyMaterial(
      new Blob(['notes'], { type: 'text/plain' }),
      'conversation-1',
    )

    assert.equal(calls.length, 10)
    for (const call of calls) {
      assert.equal(call.options.credentials, 'include')
    }
    assert.equal(
      calls[4].options.headers.get('X-CSRF-Token'),
      'test-nonce.test-signature',
    )
    assert.deepEqual(JSON.parse(calls[5].options.body), {
      question: 'What is RAG?',
      conversation_id: 'conversation-1',
      document_ids: ['document-1'],
    })
    assert.equal(calls[6].url, 'http://localhost:8000/conversations/conversation-1/documents')
    assert.equal(calls[7].options.headers.get('X-CSRF-Token'), 'test-nonce.test-signature')
    assert.deepEqual(JSON.parse(calls[7].options.body), {
      document_id: 'document-1',
    })
    assert.equal(calls[8].options.method, 'DELETE')
    assert.equal(calls[8].options.headers.get('X-CSRF-Token'), 'test-nonce.test-signature')
    assert.equal(calls[9].options.headers.has('Content-Type'), false)
    assert.equal(calls[9].options.body.get('conversation_id'), 'conversation-1')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('loginStudent and registerStudent send valid JSON payloads with credentials included', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  const api = await import('./api.js')

  try {
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      if (url.endsWith('/auth/csrf')) {
        return new Response(JSON.stringify({ csrf_token: 'auth-nonce.auth-sig' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/auth/login')) {
        const body = JSON.parse(options.body)
        if (body.password === 'wrong') {
          return new Response(JSON.stringify({ detail: 'Email or password is incorrect.' }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(
          JSON.stringify({
            user: { id: 'user-1', email: body.email, name: 'Student' },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/auth/register')) {
        const body = JSON.parse(options.body)
        return new Response(
          JSON.stringify({
            user: { id: 'user-2', email: body.email, name: body.name },
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('Not Found', { status: 404 })
    }

    const user = await api.loginStudent({
      email: 'student@example.edu',
      password: 'password123',
    })
    assert.equal(user.email, 'student@example.edu')
    const loginCall = calls.find((c) => c.url.endsWith('/auth/login'))
    assert.ok(loginCall)
    assert.equal(loginCall.options.credentials, 'include')
    assert.deepEqual(JSON.parse(loginCall.options.body), {
      email: 'student@example.edu',
      password: 'password123',
    })

    await assert.rejects(
      api.loginStudent({
        email: 'student@example.edu',
        password: 'wrong',
      }),
      { status: 401, message: 'Email or password is incorrect.' },
    )

    const registered = await api.registerStudent({
      name: 'New Student',
      email: 'new@example.edu',
      password: 'secure-password',
    })
    assert.equal(registered.name, 'New Student')
  } finally {
    globalThis.fetch = originalFetch
  }
})

