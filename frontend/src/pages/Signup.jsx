import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { GoogleLogin } from '@react-oauth/google';
import { useAuth } from '../lib/auth';
import '../auth.css';

export default function Signup() {
  const navigate = useNavigate();
  const { register, googleLogin, appleLogin, config, formatApiError } = useAuth();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) { setError('Password must be at least 8 characters.'); return; }
    setBusy(true);
    try {
      await register(email, password, name || undefined);
      navigate('/onboarding');
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const onGoogle = async (response) => {
    if (!response.credential) return;
    setError('');
    try {
      const user = await googleLogin(response.credential);
      navigate(user.onboarding_done ? '/' : '/onboarding');
    } catch (err) { setError(formatApiError(err)); }
  };

  const onApple = async () => {
    if (!config.apple_enabled) return;
    try {
      const AppleID = window.AppleID;
      if (!AppleID) { setError('Apple Sign-In is loading. Try again in a moment.'); return; }
      const nonce = crypto.randomUUID();
      AppleID.auth.init({
        clientId: config.apple_client_id, scope: 'name email',
        redirectURI: config.apple_redirect_uri,
        state: crypto.randomUUID(), nonce, usePopup: true,
      });
      const response = await AppleID.auth.signIn();
      const authorization = response.authorization || {};
      const user = await appleLogin({
        id_token: authorization.id_token, nonce, state: authorization.state, user: response.user,
      });
      navigate(user.onboarding_done ? '/' : '/onboarding');
    } catch (err) {
      setError(formatApiError(err));
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card" data-testid="signup-card">
        <div className="auth-brand"><span className="brand-mark">f</span> finaura</div>
        <h1>Create your account</h1>
        <p className="subtitle">Your financial data stays private, encrypted, and always yours.</p>

        {error && <div className="auth-error" data-testid="signup-error">{error}</div>}

        <form onSubmit={submit}>
          <div className="auth-field">
            <label>Name</label>
            <input data-testid="signup-name-input" type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your full name" autoComplete="name"/>
          </div>
          <div className="auth-field">
            <label>Email</label>
            <input data-testid="signup-email-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email"/>
          </div>
          <div className="auth-field">
            <label>Password</label>
            <input data-testid="signup-password-input" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" autoComplete="new-password"/>
            <div className="hint">Use at least 8 characters with a mix of letters and numbers.</div>
          </div>
          <button data-testid="signup-submit-button" className="auth-btn" disabled={busy} type="submit">
            {busy ? 'Creating account…' : 'Create account'}
          </button>
        </form>

        {(config.google_enabled || config.apple_enabled) && (
          <>
            <div className="auth-divider">or sign up with</div>
            <div className="social-buttons">
              {config.google_enabled && (
                <div data-testid="google-signup-container">
                  <GoogleLogin onSuccess={onGoogle} onError={() => setError('Google sign-up failed. Please try again.')} width="100%" theme="outline" text="signup_with"/>
                </div>
              )}
              {config.apple_enabled && (
                <button data-testid="apple-signup-button" className="social-btn apple" type="button" onClick={onApple}>
                  <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.53 4.08zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z"/></svg>
                  Sign up with Apple
                </button>
              )}
            </div>
          </>
        )}

        <div className="auth-footer">
          Already have an account? <Link data-testid="login-link" to="/login">Sign in</Link>
        </div>
      </div>
    </div>
  );
}
