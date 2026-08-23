import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { api, formatApiError } from './api';

const AuthContext = createContext(null);
const UNLOCK_KEY = 'finaura_unlocked';

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined); // undefined = loading, null = logged out
  const [config, setConfig] = useState({ google_enabled: false, apple_enabled: false, google_client_id: '', apple_client_id: '' });
  const [unlocked, setUnlocked] = useState(() => sessionStorage.getItem(UNLOCK_KEY) === '1');

  const loadMe = useCallback(async () => {
    const token = localStorage.getItem('finaura_token');
    if (!token) { setUser(null); return; }
    try {
      const { data } = await api.get('/auth/me');
      setUser(data);
    } catch (e) {
      localStorage.removeItem('finaura_token');
      setUser(null);
    }
  }, []);

  useEffect(() => {
    loadMe();
    api.get('/auth/config').then((r) => setConfig(r.data)).catch(() => {});
  }, [loadMe]);

  const applySession = (session) => {
    localStorage.setItem('finaura_token', session.access_token);
    setUser(session.user);
    sessionStorage.setItem(UNLOCK_KEY, session.user?.has_pin ? '0' : '1');
    setUnlocked(!session.user?.has_pin);
    return session.user;
  };

  const login = async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    return applySession(data);
  };

  const register = async (email, password, name) => {
    const { data } = await api.post('/auth/register', { email, password, name });
    return applySession(data);
  };

  const googleLogin = async (credential) => {
    const { data } = await api.post('/auth/google', { credential });
    return applySession(data);
  };

  // Used by the Google OAuth authorization-code callback page. The backend has already
  // verified the code + issued our JWT; we just adopt the session and hydrate the user.
  const applyExternalSession = async (accessToken) => {
    localStorage.setItem('finaura_token', accessToken);
    const { data: me } = await api.get('/auth/me');
    setUser(me);
    sessionStorage.setItem(UNLOCK_KEY, me?.has_pin ? '0' : '1');
    setUnlocked(!me?.has_pin);
    return me;
  };

  const appleLogin = async (payload) => {
    const { data } = await api.post('/auth/apple', payload);
    return applySession(data);
  };

  const logout = () => {
    localStorage.removeItem('finaura_token');
    sessionStorage.removeItem(UNLOCK_KEY);
    setUser(null);
    setUnlocked(false);
  };

  const forgotPassword = async (email) => api.post('/auth/forgot-password', { email }).then((r) => r.data);
  const resetPassword = async (token, new_password) => {
    const { data } = await api.post('/auth/reset-password', { token, new_password });
    return applySession(data);
  };
  const verifyEmailToken = async (token) => api.post('/auth/verify-email', { token }).then((r) => r.data);
  const resendVerification = async () => api.post('/auth/resend-verification').then((r) => r.data);

  const setPin = async (pin) => {
    const { data } = await api.post('/auth/set-pin', { pin });
    setUser((u) => (u ? { ...u, has_pin: true } : u));
    sessionStorage.setItem(UNLOCK_KEY, '1');
    setUnlocked(true);
    return data;
  };
  const verifyPin = async (pin) => {
    const { data } = await api.post('/auth/verify-pin', { pin });
    sessionStorage.setItem(UNLOCK_KEY, '1');
    setUnlocked(true);
    return data;
  };
  const removePin = async (pin) => {
    const { data } = await api.post('/auth/remove-pin', { pin });
    setUser((u) => (u ? { ...u, has_pin: false } : u));
    return data;
  };
  const lock = () => { sessionStorage.removeItem(UNLOCK_KEY); setUnlocked(false); };

  const completeOnboarding = async (choice, name) => {
    const { data } = await api.post('/auth/onboard', { choice, name });
    setUser(data);
    return data;
  };

  const refreshMe = loadMe;

  return (
    <AuthContext.Provider value={{
      user, config, unlocked,
      login, register, googleLogin, appleLogin, applyExternalSession, logout,
      forgotPassword, resetPassword, verifyEmailToken, resendVerification,
      setPin, verifyPin, removePin, lock,
      completeOnboarding, refreshMe, formatApiError,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
