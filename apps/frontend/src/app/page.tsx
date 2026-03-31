"use client";

import { useState, useCallback, useEffect } from "react";
import type { PatientContext, AppScreen } from "@/lib/types";
import { clearSession, getToken } from "@/lib/api";
import WelcomeScreen from "@/components/WelcomeScreen";
import ChatWindow from "@/components/ChatWindow";

const TOKEN_TIMEOUT_MS = 10_000; // 10 second timeout for initial connection

function fetchTokenWithTimeout(): Promise<void> {
  return Promise.race([
    getToken().then(() => {}),
    new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("Connection timeout")), TOKEN_TIMEOUT_MS),
    ),
  ]);
}

export default function Home() {
  const [screen, setScreen] = useState<AppScreen>("welcome");
  const [patientContext, setPatientContext] = useState<PatientContext | null>(null);
  const [ready, setReady] = useState(false);
  const [initError, setInitError] = useState(false);

  // Init token on mount (with timeout)
  useEffect(() => {
    if (ready || initError) return;
    fetchTokenWithTimeout()
      .then(() => setReady(true))
      .catch(() => setInitError(true));
  }, [ready, initError]);

  const handleIdentified = useCallback((ctx: PatientContext) => {
    setPatientContext(ctx);
    setScreen("chat");
  }, []);

  const handleNewChat = useCallback(() => {
    clearSession();
    setPatientContext(null);
    setScreen("welcome");
    setReady(false);
    setInitError(false);
  }, []);

  // Loading state
  if (!ready && !initError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="text-center">
          <div className="mx-auto h-8 w-8 animate-spin rounded-full border-4 border-slate-200 border-t-teal-600" />
          <p className="mt-3 text-sm text-slate-500">Connecting...</p>
        </div>
      </div>
    );
  }

  // Connection error
  if (initError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
        <div className="max-w-sm rounded-2xl bg-white p-6 text-center shadow-lg">
          <h1 className="text-lg font-semibold text-slate-800">
            Unable to connect
          </h1>
          <p className="mt-2 text-sm text-slate-600">
            We couldn&apos;t reach the server. Please check your internet
            connection and make sure the backend is running.
          </p>
          <button
            onClick={() => {
              setInitError(false);
              setReady(false);
            }}
            className="mt-4 rounded-lg bg-teal-600 px-6 py-2.5 text-sm font-medium text-white transition hover:bg-teal-700"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (screen === "welcome") {
    return <WelcomeScreen onIdentified={handleIdentified} />;
  }

  return (
    <ChatWindow
      patientContext={patientContext!}
      onNewChat={handleNewChat}
    />
  );
}
