import { api } from './api';

/**
 * Base64URL helpers (WebAuthn uses base64url for binary transport).
 */
export function b64uToBytes(value) {
  const pad = '='.repeat((4 - (value.length % 4)) % 4);
  const binary = atob(value.replace(/-/g, '+').replace(/_/g, '/') + pad);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export function bytesToB64u(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  let binary = '';
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export const passkeysSupported = () =>
  typeof window !== 'undefined' && !!window.PublicKeyCredential;

/**
 * Register a new passkey for the currently authenticated user.
 * Must be called from a user-gesture event handler.
 */
export async function registerPasskey(label = 'Passkey') {
  if (!passkeysSupported()) throw new Error('Your browser doesn\'t support passkeys.');
  const { data: options } = await api.post('/auth/passkey/register/begin');
  options.challenge = b64uToBytes(options.challenge);
  options.user.id = b64uToBytes(options.user.id);
  options.excludeCredentials = (options.excludeCredentials || []).map((x) => ({
    ...x,
    id: b64uToBytes(x.id),
  }));
  const credential = await navigator.credentials.create({ publicKey: options });
  if (!credential) throw new Error('Passkey registration cancelled.');
  const response = credential.response;
  const payload = {
    id: credential.id,
    rawId: bytesToB64u(credential.rawId),
    type: credential.type,
    label,
    response: {
      clientDataJSON: bytesToB64u(response.clientDataJSON),
      attestationObject: bytesToB64u(response.attestationObject),
      transports: response.getTransports ? response.getTransports() : [],
    },
    clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
  };
  const { data } = await api.post('/auth/passkey/register/complete', payload);
  return data;
}

/**
 * Prompt the user for a passkey assertion to unlock the app.
 * Must be called from a user-gesture event handler.
 */
export async function unlockWithPasskey() {
  if (!passkeysSupported()) throw new Error('Your browser doesn\'t support passkeys.');
  const { data: options } = await api.post('/auth/passkey/authenticate/begin');
  options.challenge = b64uToBytes(options.challenge);
  options.allowCredentials = (options.allowCredentials || []).map((x) => ({
    ...x,
    id: b64uToBytes(x.id),
  }));
  const credential = await navigator.credentials.get({ publicKey: options });
  if (!credential) throw new Error('Passkey cancelled.');
  const response = credential.response;
  const payload = {
    id: credential.id,
    rawId: bytesToB64u(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bytesToB64u(response.clientDataJSON),
      authenticatorData: bytesToB64u(response.authenticatorData),
      signature: bytesToB64u(response.signature),
      userHandle: response.userHandle ? bytesToB64u(response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
  };
  const { data } = await api.post('/auth/passkey/authenticate/complete', payload);
  return data;
}

export async function listPasskeys() {
  const { data } = await api.get('/auth/passkey/list');
  return data.credentials || [];
}

export async function removePasskey(prefix) {
  const { data } = await api.delete(`/auth/passkey/${prefix}`);
  return data;
}
