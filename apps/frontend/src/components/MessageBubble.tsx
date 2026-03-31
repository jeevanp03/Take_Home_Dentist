"use client";

import { useState, useMemo } from "react";
import type { Message } from "@/lib/types";

interface MessageBubbleProps {
  message: Message;
  onFeedback?: (messageId: string, feedback: "up" | "down") => void;
}

/* Escape HTML to prevent XSS before applying markdown formatting */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/* Lightweight markdown: bold, italic, inline code, bullet lists — applied AFTER escaping */
function renderMarkdown(text: string): string {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, '<code class="rounded bg-slate-100 px-1 py-0.5 text-sm font-mono">$1</code>')
    .replace(/^[-*] (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/\n/g, "<br />");
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/* Thumbs icons */
function ThumbUp({ filled, className }: { filled: boolean; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M7 11V3.5a1.5 1.5 0 013 0V7h3.5a2 2 0 011.94 2.48l-1.2 5A2 2 0 0112.32 16H5a2 2 0 01-2-2v-3h4z" />
    </svg>
  );
}

function ThumbDown({ filled, className }: { filled: boolean; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 20 20" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M13 9V16.5a1.5 1.5 0 01-3 0V13H6.5a2 2 0 01-1.94-2.48l1.2-5A2 2 0 017.68 4H15a2 2 0 012 2v3h-4z" />
    </svg>
  );
}

export default function MessageBubble({ message, onFeedback }: MessageBubbleProps) {
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  const isUser = message.role === "user";

  const htmlContent = useMemo(
    () => renderMarkdown(message.content),
    [message.content],
  );

  const handleFeedback = (type: "up" | "down") => {
    setFeedback(type);
    onFeedback?.(message.id, type);
  };

  return (
    <div
      className={`animate-message-in flex ${isUser ? "justify-end" : "justify-start"} mb-3`}
      role="article"
      aria-label={`${isUser ? "You" : "Mia"} said: ${message.content}`}
    >
      <div className={`max-w-[80%] ${isUser ? "order-last" : ""}`}>
        {/* Label */}
        {!isUser && (
          <span className="mb-1 block text-sm font-medium text-teal-600">
            Mia
          </span>
        )}

        {/* Bubble */}
        <div
          className={`rounded-2xl px-4 py-3 text-base leading-relaxed ${
            isUser
              ? "bg-teal-600 text-white"
              : message.error
                ? "border-2 border-red-300 bg-red-50 text-slate-800"
                : "bg-white text-slate-800 shadow-sm"
          }`}
        >
          <div
            dangerouslySetInnerHTML={{ __html: htmlContent }}
            className="[&_li]:mb-1"
          />
        </div>

        {/* Timestamp + feedback */}
        <div
          className={`mt-1.5 flex items-center gap-2 ${
            isUser ? "justify-end" : "justify-start"
          }`}
        >
          <time className="text-sm text-slate-400">
            {formatTime(message.timestamp)}
          </time>

          {/* Feedback buttons — assistant messages only */}
          {!isUser && onFeedback && !message.isStreaming && (
            <div className="flex gap-1">
              <button
                onClick={() => handleFeedback("up")}
                className={`rounded p-1.5 transition ${
                  feedback === "up" ? "text-teal-600" : "text-slate-400 hover:text-slate-600"
                }`}
                aria-label="Helpful"
                title="Helpful"
              >
                <ThumbUp filled={feedback === "up"} className="h-4 w-4" />
              </button>
              <button
                onClick={() => handleFeedback("down")}
                className={`rounded p-1.5 transition ${
                  feedback === "down" ? "text-red-500" : "text-slate-400 hover:text-slate-600"
                }`}
                aria-label="Not helpful"
                title="Not helpful"
              >
                <ThumbDown filled={feedback === "down"} className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
