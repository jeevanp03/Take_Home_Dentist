"use client";

import { useState } from "react";
import type { AppointmentDisplay } from "@/lib/types";

interface AppointmentCardProps {
  appointment: AppointmentDisplay;
  isConfirmation?: boolean;
  onConfirm?: () => void;
  onCancel?: () => void;
}

const STATUS_STYLES: Record<string, string> = {
  scheduled: "bg-teal-50 text-teal-800",
  cancelled: "bg-red-50 text-red-800",
  completed: "bg-green-50 text-green-800",
};

/* Clipboard icon SVG */
function CopyIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
    </svg>
  );
}

export default function AppointmentCard({
  appointment,
  isConfirmation = false,
  onConfirm,
  onCancel,
}: AppointmentCardProps) {
  const [copied, setCopied] = useState(false);

  const formatType = (t: string) =>
    t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  const details = `${formatType(appointment.type)}\nDate: ${appointment.date}\nTime: ${appointment.time}\nProvider: ${appointment.provider}${appointment.status ? `\nStatus: ${appointment.status}` : ""}`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(details);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: open print dialog
      window.print();
    }
  };

  return (
    <div
      className={`my-2 rounded-xl border ${
        isConfirmation
          ? "border-l-4 border-teal-600 bg-teal-50"
          : "border-slate-200 bg-white shadow-sm"
      } p-4`}
      role="article"
      aria-label={`Appointment: ${formatType(appointment.type)} on ${appointment.date}`}
    >
      {isConfirmation && (
        <p className="mb-2 text-sm font-medium text-teal-700">
          Here&apos;s what I&apos;m about to book — does this look right?
        </p>
      )}

      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h3 className="text-base font-semibold text-teal-600">
            {formatType(appointment.type)}
          </h3>
          <p className="text-sm text-slate-600">
            {appointment.date} at {appointment.time}
          </p>
          <p className="text-sm text-slate-500">
            Dr. {appointment.provider}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {appointment.status && (
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                STATUS_STYLES[appointment.status] || STATUS_STYLES.scheduled
              }`}
              aria-label={`Status: ${appointment.status}`}
            >
              {appointment.status}
            </span>
          )}
          {!isConfirmation && (
            <button
              onClick={handleCopy}
              className="rounded p-1 text-slate-400 transition hover:text-teal-600"
              aria-label={copied ? "Copied" : "Copy appointment details"}
              title={copied ? "Copied!" : "Copy details"}
            >
              {copied ? (
                <span className="text-xs font-medium text-teal-600">Copied!</span>
              ) : (
                <CopyIcon className="h-4 w-4" />
              )}
            </button>
          )}
        </div>
      </div>

      {isConfirmation && (
        <div className="mt-3 flex gap-2">
          <button
            onClick={onConfirm}
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-teal-700"
          >
            Yes, book it
          </button>
          <button
            onClick={onCancel}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
          >
            No, change something
          </button>
        </div>
      )}
    </div>
  );
}
