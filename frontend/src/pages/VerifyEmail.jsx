import { useEffect, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import '../auth.css';

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { verifyEmailToken, refreshMe, formatApiError, user } = useAuth();
  const [status, setStatus] = useState('verifying'); // verifying | success | error
  const [message, setMessage] = useState('');

  useEffect(() => {
    const token = params.get('token');
    if (!token) { setStatus('error'); setMessage('Missing verification token.'); return; }
    verifyEmailToken(token)
      .then(async () => { setStatus('success'); await refreshMe(); })
      .catch((err) => { setStatus('error'); setMessage(formatApiError(err)); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="auth-page">
      <div className="auth-card" data-testid="verify-email-card">
        <div className="auth-brand"><span className="brand-mark">f</span> FINAURA AI</div>
        <h1>{status === 'success' ? 'Email verified' : status === 'error' ? 'Something went wrong' : 'Verifying your email…'}</h1>
        <p className="subtitle">
          {status === 'success' && 'Your email has been confirmed. You\'re all set.'}
          {status === 'error' && (message || 'This verification link is invalid or has expired.')}
          {status === 'verifying' && 'Just a moment while we confirm your email.'}
        </p>
        {status === 'success' && (
          <button data-testid="verify-continue-button" className="auth-btn" onClick={() => navigate(user ? '/' : '/login')}>
            Continue to FINAURA AI
          </button>
        )}
        {status === 'error' && (
          <button className="auth-btn secondary" onClick={() => navigate('/login')}>Back to sign in</button>
        )}
      </div>
    </div>
  );
}
