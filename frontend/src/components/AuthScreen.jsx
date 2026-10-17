import { useState } from 'react'
import { AlertCircle, ArrowRight, BookOpen, LoaderCircle, ShieldCheck } from 'lucide-react'
import { ApiError, loginStudent, registerStudent } from '../services/api.js'
import './AuthScreen.css'

export default function AuthScreen({
  onAuthenticated,
  backendStatus,
  initialError,
  onRequestError,
  onRetry,
}) {
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [prevInitialError, setPrevInitialError] = useState(initialError)
  const [clearedInitialError, setClearedInitialError] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  if (initialError !== prevInitialError) {
    setPrevInitialError(initialError)
    setClearedInitialError(false)
  }

  const activeError = error || (!clearedInitialError ? initialError : '')

  async function submit(event) {
    event.preventDefault()
    if (submitting) return
    setError('')
    setClearedInitialError(true)
    setSubmitting(true)
    try {
      const user =
        mode === 'register'
          ? await registerStudent({ name, email, password })
          : await loginStudent({ email, password })
      onAuthenticated(user)
    } catch (requestError) {
      onRequestError?.(requestError)
      const message =
        requestError instanceof ApiError
          ? requestError.message
          : 'Unable to complete the request. Please try again.'
      setError(message)
    } finally {
      setSubmitting(false)
    }
  }

  function changeMode(nextMode) {
    setMode(nextMode)
    setError('')
    setClearedInitialError(true)
    setPassword('')
  }

  return (
    <main className="auth-shell">
      <section className="auth-brand-panel">
        <div className="auth-brand">
          <div className="auth-brand-mark">
            <BookOpen size={24} />
          </div>
          <div>
            <strong>EduGuide</strong>
            <span>Sri Sairam Engineering College</span>
          </div>
        </div>
        <div className="auth-intro">
          <span className="auth-overline">YOUR AI ACADEMIC COMPANION</span>
          <h1>Make every study session count.</h1>
          <p>
            Learn with clarity, ask questions about your materials, and keep
            your academic conversations in one private workspace.
          </p>
          <div className="auth-points">
            <span><ShieldCheck size={16} /> Your conversations stay with your account</span>
            <span><BookOpen size={16} /> Answers grounded in learning materials</span>
          </div>
        </div>
        <p className="auth-branding">
          EduGuide — Powered by Sri Sairam Engineering College
        </p>
      </section>

      <section className="auth-form-panel">
        <div className="auth-card">
          <div className="auth-mobile-mark"><BookOpen size={21} /></div>
          <p className="auth-kicker">
            {mode === 'login' ? 'WELCOME BACK' : 'GET STARTED'}
          </p>
          <h2>{mode === 'login' ? 'Sign in to EduGuide' : 'Create your account'}</h2>
          <p className="auth-caption">
            {mode === 'login'
              ? 'Continue learning with your saved conversations.'
              : 'Your academic chat history will be saved to your account.'}
          </p>

          {activeError && (
            <div className="auth-alert" role="alert">
              <AlertCircle size={17} />
              <span>
                {activeError}
              </span>
              {onRetry && (
                <button
                  type="button"
                  onClick={() => {
                    setError('')
                    setClearedInitialError(true)
                    onRetry?.()
                  }}
                  style={{
                    marginLeft: '8px',
                    textDecoration: 'underline',
                    background: 'none',
                    border: 'none',
                    color: 'inherit',
                    cursor: 'pointer',
                    fontWeight: 600,
                  }}
                >
                  Retry
                </button>
              )}
            </div>
          )}

          <form onSubmit={submit} className="auth-form">
            {mode === 'register' && (
              <label>
                <span>Full name</span>
                <input
                  type="text"
                  name="name"
                  autoComplete="name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Your name"
                  maxLength={100}
                  required
                />
              </label>
            )}
            <label>
              <span>College email</span>
              <input
                type="email"
                name="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                maxLength={254}
                required
              />
            </label>
            <label>
              <span>Password</span>
              <input
                type="password"
                name="password"
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={mode === 'register' ? 'At least 10 characters' : 'Your password'}
                minLength={mode === 'register' ? 10 : 1}
                maxLength={128}
                required
              />
            </label>
            <button
              className="auth-submit"
              type="submit"
              disabled={submitting || backendStatus === 'offline'}
            >
              {submitting ? (
                <>
                  <LoaderCircle className="auth-spinner" size={17} />
                  {mode === 'login' ? 'Signing in…' : 'Creating account…'}
                </>
              ) : (
                <>
                  {mode === 'login' ? 'Sign in' : 'Create account'}
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </form>

          <p className="auth-switch">
            {mode === 'login' ? 'New to EduGuide?' : 'Already have an account?'}
            <button
              type="button"
              onClick={() => changeMode(mode === 'login' ? 'register' : 'login')}
            >
              {mode === 'login' ? 'Create an account' : 'Sign in'}
            </button>
          </p>
          <p className="auth-security-note">
            <ShieldCheck size={14} />
            Passwords are securely hashed. Sessions use protected cookies.
          </p>
        </div>
      </section>
    </main>
  )
}
