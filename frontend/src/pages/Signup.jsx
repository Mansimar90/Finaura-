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

  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  const onContinueWithGoogle = () => {
    const backend = process.env.REACT_APP_BACKEND_URL;
    window.location.href = `${backend}/api/auth/google/start?next=/`;
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
        <div className="auth-brand"><span className="brand-mark">f</span> FINAURA AI</div>
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
              {config.google_authcode_enabled ? (
                <button
                  type="button"
                  data-testid="google-signup-button"
                  className="social-btn google"
                  onClick={onContinueWithGoogle}
                  disabled={busy}
                >
                  <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
                    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
                    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
                    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
                    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
                  </svg>
                  Continue with Google
                </button>
              ) : (
                config.google_enabled && (
                  <div data-testid="google-signup-container">
                    <GoogleLogin onSuccess={onGoogle} onError={() => setError('Google sign-up failed. Please try again.')} width="100%" theme="outline" text="signup_with"/>
                  </div>
                )
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
