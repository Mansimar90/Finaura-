import { useState } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import '../auth.css';

export default function ResetPassword() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { resetPassword, formatApiError } = useAuth();
  const token = params.get('token') || '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (!token) { setError('Missing reset token. Use the link from your email.'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters.'); return; }
    if (password !== confirm) { setError('Passwords don\'t match.'); return; }
    setBusy(true);
    try {
      const user = await resetPassword(token, password);
      if (!user.onboarding_done) navigate('/onboarding');
      else if (user.has_pin) navigate('/lock');
      else navigate('/');
    } catch (err) {
      setError(formatApiError(err));
    } finally { setBusy(false); }
  };

  return (
    <div className="auth-page">
      <div className="auth-card" data-testid="reset-card">
        <div className="auth-brand"><span className="brand-mark">f</span> FINAURA AI</div>
        <h1>Set a new password</h1>
        <p className="subtitle">Choose a strong password you'll remember.</p>

        {error && <div className="auth-error" data-testid="reset-error">{error}</div>}

        <form onSubmit={submit}>
          <div className="auth-field">
            <label>New password</label>
            <input data-testid="reset-password-input" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" autoComplete="new-password"/>
          </div>
          <div className="auth-field">
            <label>Confirm new password</label>
            <input data-testid="reset-confirm-input" type="password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Repeat password" autoComplete="new-password"/>
          </div>
          <button data-testid="reset-submit-button" className="auth-btn" disabled={busy} type="submit">
            {busy ? 'Updating…' : 'Update password'}
          </button>
        </form>

        <div className="auth-footer">
          <Link to="/login">← Back to sign in</Link>
        </div>
      </div>
    </div>
  );
}
