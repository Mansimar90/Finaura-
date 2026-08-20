import { useState } from 'react';
import { useNavigate, Link, useLocation } from 'react-router-dom';
import { GoogleLogin } from '@react-oauth/google';
import { useAuth } from '../lib/auth';
import '../auth.css';

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, googleLogin, appleLogin, config, formatApiError } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const next = new URLSearchParams(location.search).get('next') || '/';

  const routeAfter = (user) => {
    if (!user.onboarding_done) navigate('/onboarding');
    else if (user.has_pin) navigate('/lock', { state: { next } });
    else navigate(next);
  };

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      const user = await login(email, password);
      routeAfter(user);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const onGoogle = async (response) => {
    if (!response.credential) { setError('Google returned no credential'); return; }
    setError('');
    setBusy(true);
    try {
      const user = await googleLogin(response.credential);
      routeAfter(user);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setBusy(false);
    }
  };

  const onApple = async () => {
    if (!config.apple_enabled) return;
    try {
      const AppleID = window.AppleID;
      if (!AppleID) { setError('Apple Sign-In is loading. Try again in a moment.'); return; }
      const nonce = crypto.randomUUID();
      AppleID.auth.init({
        clientId: config.apple_client_id,
        scope: 'name email',
        redirectURI: config.apple_redirect_uri,
        state: crypto.randomUUID(),
        nonce,
        usePopup: true,
      });
      const response = await AppleID.auth.signIn();
      const authorization = response.authorization || {};
      const user = await appleLogin({
        id_token: authorization.id_token,
        nonce,
        state: authorization.state,
        user: response.user,
      });
      routeAfter(user);
    } catch (err) {
      setError(formatApiError(err));
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card" data-testid="login-card">
        <div className="auth-brand"><span className="brand-mark">f</span> finaura</div>
        <h1>Welcome back</h1>
        <p className="subtitle">Sign in to your Finaura account to continue.</p>

        {error && <div className="auth-error" data-testid="login-error">{error}</div>}

        <form onSubmit={submit}>
          <div className="auth-field">
            <label>Email</label>
            <input data-testid="login-email-input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" autoComplete="email"/>
          </div>
          <div className="auth-field">
            <label>Password</label>
            <input data-testid="login-password-input" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" autoComplete="current-password"/>
          </div>
          <button data-testid="login-submit-button" className="auth-btn" disabled={busy} type="submit">
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <div style={{ textAlign: 'right', marginTop: 10 }}>
          <Link data-testid="forgot-password-link" to="/forgot-password" className="auth-link-btn" style={{ textDecoration: 'none' }}>Forgot password?</Link>
        </div>

        {(config.google_enabled || config.apple_enabled) && (
          <>
            <div className="auth-divider">or continue with</div>
            <div className="social-buttons">
              {config.google_enabled && (
                <div data-testid="google-signin-container">
                  <GoogleLogin onSuccess={onGoogle} onError={() => setError('Google sign-in failed. Please try again.')} width="100%" theme="outline"/>
                </div>
              )}
              {config.apple_enabled && (
                <button data-testid="apple-signin-button" className="social-btn apple" type="button" onClick={onApple}>
                  <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16"><path d="M17.05 20.28c-.98.95-2.05.8-3.08.35-1.09-.46-2.09-.48-3.24 0-1.44.62-2.2.44-3.06-.35C2.79 15.25 3.51 7.59 9.05 7.31c1.35.07 2.29.74 3.08.8 1.18-.24 2.31-.93 3.57-.84 1.51.12 2.65.72 3.4 1.8-3.12 1.87-2.38 5.98.48 7.13-.57 1.5-1.31 2.99-2.53 4.08zM12.03 7.25c-.15-2.23 1.66-4.07 3.74-4.25.29 2.58-2.34 4.5-3.74 4.25z"/></svg>
                  Sign in with Apple
                </button>
              )}
            </div>
          </>
        )}

        {!config.google_enabled && !config.apple_enabled && (
          <div className="auth-provider-badge" data-testid="oauth-not-configured">
            Google and Apple Sign-In will appear once your API keys are added to <code>backend/.env</code>.
          </div>
        )}

        <div className="auth-footer">
          New to Finaura?{' '}
          <Link data-testid="signup-link" to="/signup">Create your account</Link>
        </div>

        <div className="auth-guest">
          Just exploring?
          <div><button data-testid="try-demo-button" onClick={() => navigate('/demo')}>Continue as guest — try the demo</button></div>
        </div>
      </div>
    </div>
  );
}
