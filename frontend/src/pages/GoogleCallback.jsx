// REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import '../auth.css';

const ERROR_MESSAGES = {
  google_cancelled: 'Google sign-in was cancelled. You can try again anytime.',
  google_not_configured: 'Google Sign-In is not configured for this environment.',
  invalid_state: 'This sign-in link has expired or was already used. Please try again.',
  expired_state: 'This sign-in link has expired. Please try again.',
  state_mismatch: 'Security check failed. Please try again from the login page.',
  missing_code_or_state: 'Sign-in response was incomplete. Please try again.',
  token_exchange_failed: 'Could not verify your Google account. Please try again.',
  network_error: 'Network error while contacting Google. Please try again.',
  no_id_token: 'Google did not return the required credentials. Please try again.',
  invalid_id_token: 'Google credentials could not be verified. Please try again.',
  invalid_issuer: 'Invalid Google response. Please try again.',
  missing_sub: 'Google did not return your account ID. Please try again.',
  signin_failed: 'We could not sign you in. Please try another method.',
  google_error: 'Google returned an error. Please try again.',
};

export default function GoogleCallback() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { applyExternalSession, refreshMe } = useAuth();
  const [status, setStatus] = useState('processing'); // processing | error
  const [errorMsg, setErrorMsg] = useState('');
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return; // guard against React StrictMode double-invoke
    ran.current = true;
    (async () => {
      // The backend redirects here with data in the URL FRAGMENT (#access_token=...&provider=google)
      // Fragments never hit servers/logs. Read via window.location.hash.
      const hash = (window.location.hash || '').replace(/^#/, '');
      const fragParams = new URLSearchParams(hash);
      const access_token = fragParams.get('access_token');
      const errCode = fragParams.get('error') || params.get('error');
      const next = params.get('next') || '/';

      // Immediately wipe the fragment so the token doesn't linger in the URL bar / history
      try { window.history.replaceState(null, '', window.location.pathname + window.location.search); } catch (_) {}

      if (errCode) {
        setStatus('error');
        setErrorMsg(ERROR_MESSAGES[errCode] || 'Google sign-in failed. Please try again.');
        return;
      }
      if (!access_token) {
        setStatus('error');
        setErrorMsg('Missing sign-in token. Please try again.');
        return;
      }
      try {
        const user = await applyExternalSession(access_token);
        // Route just like the normal login flow
        if (!user.onboarding_done) navigate('/onboarding', { replace: true });
        else if (user.has_pin) navigate('/lock', { replace: true, state: { next } });
        else navigate(next, { replace: true });
      } catch (e) {
        setStatus('error');
        setErrorMsg('Could not complete sign-in. Please try again.');
        try { await refreshMe(); } catch (_) {}
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="auth-page">
      <div className="auth-card" data-testid="google-callback-card">
        <div className="auth-brand"><span className="brand-mark">f</span> FINAURA AI</div>
        {status === 'processing' && (
          <>
            <h1>Signing you in…</h1>
            <p className="subtitle">Verifying your Google account. This will only take a moment.</p>
            <div style={{ marginTop: 24, display: 'flex', justifyContent: 'center' }}>
              <div className="loader-spinner" data-testid="google-callback-spinner" />
            </div>
          </>
        )}
        {status === 'error' && (
          <>
            <h1>Sign-in failed</h1>
            <div className="auth-error" data-testid="google-callback-error">{errorMsg}</div>
            <button
              className="auth-btn"
              data-testid="google-callback-back-btn"
              style={{ marginTop: 16 }}
              onClick={() => navigate('/login', { replace: true })}
            >
              Back to sign in
            </button>
          </>
        )}
      </div>
    </div>
  );
}
