/**
 * API client — handles auth tokens, patient identification, and SSE chat streaming.
 *
 * Tokens are kept in memory (not localStorage) per security spec.
 * Auto-refreshes at 50 min; retries once on 401.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---------------------------------------------------------------------------
// Token management (in-memory only)
// ---------------------------------------------------------------------------

let _token: string | null = null;
let _sessionId: string | null = null;
let _refreshTimer: ReturnType<typeof setTimeout> | null = null;
let _tokenPromise: Promise<TokenResponse> | null = null; // guards concurrent fetches

const REFRESH_MS = 50 * 60 * 1000; // 50 minutes

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  session_id: string;
}

async function fetchToken(): Promise<TokenResponse> {
  const res = await fetch(`${API_URL}/api/auth/token`, { method: "POST" });
  if (!res.ok) throw new Error(`Token request failed: ${res.status}`);
  return res.json();
}

async function refreshToken(): Promise<TokenResponse> {
  const res = await fetch(`${API_URL}/api/auth/refresh`, {
    method: "POST",
    headers: { Authorization: `Bearer ${_token}` },
  });
  if (!res.ok) throw new Error(`Token refresh failed: ${res.status}`);
  return res.json();
}

function clearRefreshTimer() {
  if (_refreshTimer) clearTimeout(_refreshTimer);
  _refreshTimer = null;
}

function scheduleRefresh() {
  clearRefreshTimer();
  _refreshTimer = setTimeout(async () => {
    try {
      const data = await refreshToken();
      _token = data.access_token;
      _sessionId = data.session_id;
      scheduleRefresh();
    } catch {
      _token = null;
      _sessionId = null;
      clearRefreshTimer();
    }
  }, REFRESH_MS);
}

export async function getToken(): Promise<{ token: string; sessionId: string }> {
  if (_token && _sessionId) return { token: _token, sessionId: _sessionId };

  // Guard: reuse in-flight token request to prevent race conditions
  if (_tokenPromise) {
    const data = await _tokenPromise;
    return { token: data.access_token, sessionId: data.session_id };
  }

  _tokenPromise = fetchToken();
  try {
    const data = await _tokenPromise;
    _token = data.access_token;
    _sessionId = data.session_id;
    scheduleRefresh();
    return { token: _token, sessionId: _sessionId };
  } finally {
    _tokenPromise = null;
  }
}

export function getSessionId(): string | null {
  return _sessionId;
}

export function clearSession() {
  _token = null;
  _sessionId = null;
  _tokenPromise = null;
  clearRefreshTimer();
}

// ---------------------------------------------------------------------------
// Authenticated fetch wrapper (auto-retry on 401)
// ---------------------------------------------------------------------------

async function authFetch(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const { token } = await getToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...init.headers,
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });

  if (res.status === 401 && retry) {
    _token = null;
    _sessionId = null;
    clearRefreshTimer();
    return authFetch(path, init, false);
  }

  return res;
}

// ---------------------------------------------------------------------------
// Patient identification
// ---------------------------------------------------------------------------

export interface IdentifyResponse {
  status: string;
  patient_id: string | null;
  patient_name: string | null;
  upcoming_appointments: AppointmentInfo[];
  needs_info: string[];
  message: string | null;
}

export interface AppointmentInfo {
  id: string;
  type: string;
  date: string;
  time: string;
  provider: string;
}

export async function identifyPatient(
  mode: "returning" | "new" | "question",
  name?: string,
  phone?: string,
): Promise<IdentifyResponse> {
  const res = await authFetch("/api/identify", {
    method: "POST",
    body: JSON.stringify({ mode, name, phone }),
  });
  if (!res.ok) throw new Error(`Identify failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Chat — SSE streaming
// ---------------------------------------------------------------------------

export interface ChatChunk {
  type: "text" | "error" | "tool_status";
  content: string;
}

const STREAM_TIMEOUT_MS = 60_000; // 60 seconds max silence

export async function* sendMessage(
  message: string,
): AsyncGenerator<ChatChunk, void, unknown> {
  const { token } = await getToken();

  const res = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message }),
  });

  if (res.status === 401) {
    _token = null;
    _sessionId = null;
    clearRefreshTimer();
    const { token: newToken } = await getToken();
    const retry = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${newToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message }),
    });
    if (!retry.ok) throw new Error(`Chat failed: ${retry.status}`);
    yield* parseSSE(retry);
    return;
  }

  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  yield* parseSSE(res);
}

async function* parseSSE(
  res: Response,
): AsyncGenerator<ChatChunk, void, unknown> {
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      // Timeout: abort if no data for STREAM_TIMEOUT_MS
      const readPromise = reader.read();
      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("Stream timeout")), STREAM_TIMEOUT_MS),
      );

      const { done, value } = await Promise.race([readPromise, timeoutPromise]);
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Split on double-newline (SSE event boundary) for robustness,
      // but also handle single-newline separated data lines
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;

        const payload = trimmed.slice(5).trim();
        if (payload === "[DONE]") return;

        try {
          const chunk: ChatChunk = JSON.parse(payload);
          if (chunk.content) yield chunk;
        } catch {
          // Skip malformed chunks
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Slots
// ---------------------------------------------------------------------------

export interface SlotInfo {
  id: string;
  date: string;
  date_iso: string;
  start_time: string;
  end_time: string;
  provider_name: string;
}

export async function getSlots(
  dateStart?: string,
  dateEnd?: string,
  provider?: string,
): Promise<{ slots: SlotInfo[]; total: number }> {
  const params = new URLSearchParams();
  if (dateStart) params.set("date_start", dateStart);
  if (dateEnd) params.set("date_end", dateEnd);
  if (provider) params.set("provider", provider);

  const qs = params.toString();
  const res = await authFetch(`/api/slots${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Slots failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------

export async function submitFeedback(
  messageId: string,
  feedback: "up" | "down",
): Promise<void> {
  await authFetch("/api/feedback", {
    method: "POST",
    body: JSON.stringify({ message_id: messageId, feedback }),
  });
}

// ---------------------------------------------------------------------------
// Health check (no auth)
// ---------------------------------------------------------------------------

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/api/health`);
    return res.ok;
  } catch {
    return false;
  }
}
