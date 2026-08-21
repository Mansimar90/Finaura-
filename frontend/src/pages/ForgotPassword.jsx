import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import '../auth.css';

export default function ForgotPassword() {
  const { forgotPassword, formatApiError } = useAuth();
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(''); setMessage(''); setBusy(true);
    try {
      const r = await forgotPassword(email);
      setMessage(r.message || 'If that email is registered, a reset link is on its way.');
    } catch (err) {
      setError(formatApiError(err));
    } finally { setBusy(false); }
  };

  return (
    <div className="auth-page">
      <div className="auth-card" data-testid="forgot-card">
        <div className="auth-brand"><span className="brand-mark">f</span> FINAURA AI</div>
        <h1>Reset your password</h1>
        <p className="subtitle">Enter your email and we'll send a secure reset link.</p>

        {error && <div className="auth-error">{error}</div>}
        {message && <div className="auth-success" data-testid="forgot-success">{message}</div>}

        <div className="auth-note">
          If email delivery isn't configured yet, the reset link will be printed in the backend server logs so you can still test the flow.
        </div>

        <form onSubmit={submit}>
          <div className="auth-field">
            <label>Email</label>
            <input data-testid="forgot-email-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com"/>
          </div>
          <button data-testid="forgot-submit-button" className="auth-btn" disabled={busy} type="submit">
            {busy ? 'Sending…' : 'Send reset link'}
          </button>
        </form>

        <div className="auth-footer">
          <Link data-testid="back-to-login-link" to="/login">← Back to sign in</Link>
        </div>
      </div>
    </div>
  );
}
