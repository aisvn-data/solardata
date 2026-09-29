import React from 'react'

/**
 * Catches unhandled rendering errors and prevents white-screen crashes.
 *
 * Provides a "Go back" button that restores the previous working state via
 * the browser History API, and automatically clears the error state when the
 * user navigates via browser back/forward buttons or hash changes.
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error('[solardata] Uncaught UI render error:', error, errorInfo)
  }

  componentDidMount() {
    if (typeof window !== 'undefined') {
      window.addEventListener('popstate', this.handleNavigation)
      window.addEventListener('hashchange', this.handleNavigation)
    }
  }

  componentWillUnmount() {
    if (typeof window !== 'undefined') {
      window.removeEventListener('popstate', this.handleNavigation)
      window.removeEventListener('hashchange', this.handleNavigation)
    }
  }

  handleNavigation = () => {
    if (this.state.hasError) {
      this.setState({ hasError: false, error: null })
    }
  }

  handleBack = () => {
    this.setState({ hasError: false, error: null })
    if (typeof window !== 'undefined' && window.history.length > 1) {
      window.history.back()
    } else if (typeof window !== 'undefined') {
      window.location.hash = '#explore'
    }
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
    if (typeof window !== 'undefined') {
      window.location.hash = '#explore'
      window.location.reload()
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="error-boundary-fallback"
          style={{
            padding: '24px',
            margin: '20px 0',
            border: '1px solid #feb2b2',
            borderRadius: '8px',
            background: '#fff5f5',
            color: '#742a2a',
          }}
          role="alert"
        >
          <h3 style={{ margin: '0 0 10px 0', color: '#9b2c2c' }}>
            Something went wrong while rendering this view
          </h3>
          <p style={{ margin: '0 0 16px 0', fontSize: '0.95rem' }}>
            {this.state.error?.message || 'An unexpected rendering error occurred.'}
          </p>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={this.handleBack}
              style={{
                cursor: 'pointer',
                padding: '6px 14px',
                borderRadius: '4px',
                border: '1px solid #9b2c2c',
                background: '#9b2c2c',
                color: '#fff',
                fontWeight: 600,
              }}
            >
              ← Go back to previous view
            </button>
            <button
              type="button"
              className="btn"
              onClick={this.handleReset}
              style={{
                cursor: 'pointer',
                padding: '6px 14px',
                borderRadius: '4px',
                border: '1px solid #cbd5e0',
                background: '#edf2f7',
                color: '#2d3748',
              }}
            >
              Reset to default
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
