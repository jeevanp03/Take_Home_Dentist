"use client";

import { useState, useRef, useCallback } from "react";
import { identifyPatient } from "@/lib/api";
import type { PatientMode, PatientContext } from "@/lib/types";

interface WelcomeScreenProps {
  onIdentified: (context: PatientContext) => void;
}

type Step = "choose" | "form" | "not-found";

/* Tooth SVG icon */
function ToothIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M32 4C24.5 4 20 8 18 12C16 16 14 18 10 18C6 18 4 22 4 26C4 30 6 34 10 34C12 34 14 36 15 40C16 44 17 52 20 56C23 60 26 60 28 56C30 52 31 46 32 42C33 46 34 52 36 56C38 60 41 60 44 56C47 52 48 44 49 40C50 36 52 34 54 34C58 34 60 30 60 26C60 22 58 18 54 18C50 18 48 16 46 12C44 8 39.5 4 32 4Z"
        fill="currentColor"
      />
    </svg>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

export default function WelcomeScreen({ onIdentified }: WelcomeScreenProps) {
  const [step, setStep] = useState<Step>("choose");
  const [mode, setMode] = useState<PatientMode>("question");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const handleModeSelect = useCallback(
    async (selectedMode: PatientMode) => {
      setMode(selectedMode);
      setError(null);

      if (selectedMode === "question") {
        setLoading(true);
        try {
          await identifyPatient("question");
          onIdentified({
            patientId: null,
            patientName: null,
            mode: "question",
            upcomingAppointments: [],
            needsInfo: [],
          });
        } catch {
          setError("Could not connect to the server. Please try again.");
          setLoading(false);
        }
        return;
      }

      setStep("form");
      setTimeout(() => nameRef.current?.focus(), 100);
    },
    [onIdentified],
  );

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!name.trim() || !phone.trim()) {
        setError("Please enter both your name and phone number.");
        return;
      }

      // Basic phone validation: at least 10 digits
      const digits = phone.replace(/\D/g, "");
      if (digits.length < 10) {
        setError("Please enter a valid phone number (e.g., (555) 123-4567).");
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const res = await identifyPatient(mode, name.trim(), phone.trim());

        if (res.status === "error") {
          setError(res.message || "Something went wrong. Please try again.");
          setLoading(false);
          return;
        }

        if (res.status === "not_found") {
          setStep("not-found");
          setLoading(false);
          return;
        }

        // Success — "ok" or "existing"
        onIdentified({
          patientId: res.patient_id,
          patientName: res.patient_name,
          mode: res.status === "existing" ? "returning" : mode,
          upcomingAppointments: (res.upcoming_appointments || []).map((a) => ({
            id: a.id,
            type: a.type,
            date: a.date,
            time: a.time,
            provider: a.provider,
          })),
          needsInfo: res.needs_info || [],
        });
      } catch {
        setError("Could not connect to the server. Please try again.");
        setLoading(false);
      }
    },
    [mode, name, phone, onIdentified],
  );

  const handleRegisterNew = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await identifyPatient("new", name.trim(), phone.trim());
      if (res.status === "error") {
        setError(res.message || "Something went wrong.");
        setLoading(false);
        return;
      }
      onIdentified({
        patientId: res.patient_id,
        patientName: res.patient_name,
        mode: "new",
        upcomingAppointments: [],
        needsInfo: res.needs_info || [],
      });
    } catch {
      setError("Could not connect. Please try again.");
      setLoading(false);
    }
  }, [name, phone, onIdentified]);

  const resetToChoose = () => {
    setStep("choose");
    setError(null);
    setName("");
    setPhone("");
    setLoading(false);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <div className="w-full max-w-md" role="main">
        {/* Branding */}
        <div className="mb-8 text-center">
          <ToothIcon className="mx-auto h-14 w-14 text-teal-600" />
          <h1 className="mt-4 text-2xl font-bold text-slate-800">
            Bright Smile Dental
          </h1>
          <p className="mt-2 text-slate-600">
            Welcome! I&apos;m Mia, your dental assistant.
          </p>
        </div>

        {/* ARIA live region for announcements */}
        <div aria-live="polite" aria-atomic="true" className="sr-only">
          {error && error}
          {loading && "Loading, please wait."}
          {step === "not-found" && "Patient not found."}
        </div>

        {/* Card */}
        <div className="rounded-2xl bg-white p-6 shadow-lg">
          {/* Step: Choose mode */}
          {step === "choose" && !loading && (
            <div className="space-y-3">
              <h2 className="mb-4 text-center text-lg font-semibold text-slate-700">
                How can I help you today?
              </h2>

              <button
                onClick={() => handleModeSelect("returning")}
                className="w-full rounded-xl border-2 border-slate-200 bg-white px-5 py-4 text-left transition hover:border-teal-600 hover:bg-teal-50 focus-visible:ring-2 focus-visible:ring-teal-600"
              >
                <span className="block text-base font-medium text-slate-800">
                  I&apos;ve been here before
                </span>
                <span className="mt-0.5 block text-sm text-slate-500">
                  Look up my existing records
                </span>
              </button>

              <button
                onClick={() => handleModeSelect("new")}
                className="w-full rounded-xl border-2 border-slate-200 bg-white px-5 py-4 text-left transition hover:border-teal-600 hover:bg-teal-50 focus-visible:ring-2 focus-visible:ring-teal-600"
              >
                <span className="block text-base font-medium text-slate-800">
                  I&apos;m a new patient
                </span>
                <span className="mt-0.5 block text-sm text-slate-500">
                  Get started with Bright Smile Dental
                </span>
              </button>

              <button
                onClick={() => handleModeSelect("question")}
                className="w-full rounded-xl border-2 border-slate-200 bg-white px-5 py-4 text-left transition hover:border-teal-600 hover:bg-teal-50 focus-visible:ring-2 focus-visible:ring-teal-600"
              >
                <span className="block text-base font-medium text-slate-800">
                  Just have a question
                </span>
                <span className="mt-0.5 block text-sm text-slate-500">
                  Ask Mia anything about dental care
                </span>
              </button>
            </div>
          )}

          {/* Step: Name + Phone form */}
          {step === "form" && !loading && (
            <form onSubmit={handleSubmit} className="space-y-4">
              <h2 className="text-center text-lg font-semibold text-slate-700">
                {mode === "returning"
                  ? "Let me find your records"
                  : "Let's get you set up"}
              </h2>

              <div>
                <label
                  htmlFor="patient-name"
                  className="mb-1 block text-sm font-medium text-slate-700"
                >
                  Full Name
                </label>
                <input
                  ref={nameRef}
                  id="patient-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Jane Smith"
                  className="w-full rounded-lg border border-slate-300 px-4 py-3 text-base text-slate-800 placeholder-slate-500 transition focus:border-teal-600 focus:ring-2 focus:ring-teal-600 focus:outline-none"
                  autoComplete="name"
                  required
                />
              </div>

              <div>
                <label
                  htmlFor="patient-phone"
                  className="mb-1 block text-sm font-medium text-slate-700"
                >
                  Phone Number
                </label>
                <input
                  id="patient-phone"
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="(555) 123-4567"
                  className="w-full rounded-lg border border-slate-300 px-4 py-3 text-base text-slate-800 placeholder-slate-500 transition focus:border-teal-600 focus:ring-2 focus:ring-teal-600 focus:outline-none"
                  autoComplete="tel"
                  required
                />
              </div>

              {error && (
                <p className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600" role="alert">
                  {error}
                </p>
              )}

              <button
                type="submit"
                className="w-full rounded-lg bg-teal-600 px-4 py-3 text-base font-medium text-white transition hover:bg-teal-700 focus-visible:ring-2 focus-visible:ring-teal-600 focus-visible:ring-offset-2"
              >
                Continue
              </button>

              <button
                type="button"
                onClick={resetToChoose}
                className="w-full text-center text-sm text-slate-500 hover:text-teal-600"
              >
                Start over
              </button>
            </form>
          )}

          {/* Step: Patient not found */}
          {step === "not-found" && !loading && (
            <div className="space-y-4 text-center">
              <h2 className="text-lg font-semibold text-slate-700">
                We couldn&apos;t find your record
              </h2>
              <p className="text-sm text-slate-600">
                We don&apos;t have an account matching that name and phone number. Would
                you like to register as a new patient?
              </p>

              {error && (
                <p className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600" role="alert">
                  {error}
                </p>
              )}

              <button
                onClick={handleRegisterNew}
                className="w-full rounded-lg bg-teal-600 px-4 py-3 text-base font-medium text-white transition hover:bg-teal-700"
              >
                Register as new patient
              </button>

              <button
                onClick={resetToChoose}
                className="w-full text-center text-sm text-slate-500 hover:text-teal-600"
              >
                Start over
              </button>
            </div>
          )}

          {/* Loading state */}
          {loading && (
            <div className="flex flex-col items-center justify-center py-8">
              <Spinner />
              <p className="mt-3 text-sm text-slate-500">One moment...</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
