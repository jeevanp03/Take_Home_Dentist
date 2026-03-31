"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { Message, PatientContext } from "@/lib/types";
import { sendMessage, submitFeedback } from "@/lib/api";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import TypingIndicator from "./TypingIndicator";
import QuickReplies from "./QuickReplies";

interface ChatWindowProps {
  patientContext: PatientContext;
  onNewChat: () => void;
  sessionWarning?: boolean;
}

const MAX_MESSAGES = 200;

function generateId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function buildGreeting(ctx: PatientContext): string {
  if (ctx.mode === "returning" && ctx.patientName) {
    let greeting = `Welcome back, ${ctx.patientName}! I'm Mia, your dental assistant at Bright Smile Dental.`;
    if (ctx.upcomingAppointments.length > 0) {
      greeting += "\n\nYou have upcoming appointments:";
      for (const appt of ctx.upcomingAppointments) {
        greeting += `\n- **${appt.type.replace(/_/g, " ")}** on ${appt.date} at ${appt.time} with Dr. ${appt.provider}`;
      }
      greeting += "\n\nHow can I help you today?";
    } else {
      greeting += " How can I help you today?";
    }
    return greeting;
  }
  if (ctx.mode === "new" && ctx.patientName) {
    return `Welcome to Bright Smile Dental, ${ctx.patientName}! I'm Mia, and I'll help you get set up. I just need a couple more details — what's your date of birth?`;
  }
  return "Hi there! I'm Mia, the dental assistant at Bright Smile Dental. How can I help you today?";
}

/* Header logo */
function ToothIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <path
        d="M32 4C24.5 4 20 8 18 12C16 16 14 18 10 18C6 18 4 22 4 26C4 30 6 34 10 34C12 34 14 36 15 40C16 44 17 52 20 56C23 60 26 60 28 56C30 52 31 46 32 42C33 46 34 52 36 56C38 60 41 60 44 56C47 52 48 44 49 40C50 36 52 34 54 34C58 34 60 30 60 26C60 22 58 18 54 18C50 18 48 16 46 12C44 8 39.5 4 32 4Z"
        fill="currentColor"
      />
    </svg>
  );
}

function addMessage(prev: Message[], msg: Message): Message[] {
  const updated = [...prev, msg];
  return updated.length > MAX_MESSAGES
    ? updated.slice(updated.length - MAX_MESSAGES)
    : updated;
}

export default function ChatWindow({
  patientContext,
  onNewChat,
  sessionWarning = false,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>(() => [
    {
      id: generateId(),
      role: "assistant",
      content: buildGreeting(patientContext),
      timestamp: new Date(),
    },
  ]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [toolStatus, setToolStatus] = useState<string | undefined>();
  const [showQuickReplies, setShowQuickReplies] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedbackToast, setFeedbackToast] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  // Auto-scroll — respect user scroll position and prefers-reduced-motion
  useEffect(() => {
    if (userScrolledUp.current) return;
    const prefersReduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    messagesEndRef.current?.scrollIntoView({
      behavior: prefersReduced ? "auto" : "smooth",
    });
  }, [messages, isStreaming]);

  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const threshold = 100;
    userScrolledUp.current =
      el.scrollHeight - el.scrollTop - el.clientHeight > threshold;
  };

  const handleSend = useCallback(
    async (text: string) => {
      setShowQuickReplies(false);
      setError(null);

      const userMsg: Message = {
        id: generateId(),
        role: "user",
        content: text,
        timestamp: new Date(),
      };
      setMessages((prev) => addMessage(prev, userMsg));

      const assistantId = generateId();
      setMessages((prev) =>
        addMessage(prev, {
          id: assistantId,
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
        }),
      );

      setIsStreaming(true);
      setToolStatus(undefined);

      try {
        for await (const chunk of sendMessage(text)) {
          if (chunk.type === "tool_status") {
            setToolStatus(chunk.content);
            continue;
          }

          if (chunk.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: chunk.content, isStreaming: false, error: true }
                  : m,
              ),
            );
            break;
          }

          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: m.content + chunk.content }
                : m,
            ),
          );
          setToolStatus(undefined);
        }

        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, isStreaming: false } : m,
          ),
        );
      } catch {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: "Something went wrong — please try again.",
                  isStreaming: false,
                  error: true,
                }
              : m,
          ),
        );
        setError("Connection issue. You can retry your message.");
      } finally {
        setIsStreaming(false);
        setToolStatus(undefined);
      }
    },
    [],
  );

  const handleFeedback = useCallback(
    (messageId: string, feedback: "up" | "down") => {
      submitFeedback(messageId, feedback)
        .then(() => {
          setFeedbackToast(true);
          setTimeout(() => setFeedbackToast(false), 2000);
        })
        .catch((err) => {
          console.error("Feedback submission failed:", err);
        });
    },
    [],
  );

  const handleRetry = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;

    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.error) return prev.slice(0, -1);
      return prev;
    });

    setError(null);
    handleSend(lastUser.content);
  }, [messages, handleSend]);

  return (
    <div className="flex h-screen flex-col bg-slate-50">
      {/* Header */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center gap-2" role="banner" aria-label="Bright Smile Dental">
          <ToothIcon className="h-7 w-7 text-teal-600" />
          <div>
            <h1 className="text-base font-semibold text-slate-800">
              Bright Smile Dental
            </h1>
            <p className="text-xs text-slate-500">Chat with Mia</p>
          </div>
        </div>
        <button
          onClick={onNewChat}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 transition hover:bg-slate-50 hover:text-teal-600"
          aria-label="Start a new chat"
        >
          New Chat
        </button>
      </header>

      {/* Session timeout warning */}
      {sessionWarning && (
        <div
          className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-sm text-amber-700"
          role="alert"
        >
          This session will reset soon — are you still there?
        </div>
      )}

      {/* Messages */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="chat-scroll flex-1 overflow-y-auto px-4 py-4"
        role="log"
        aria-live="polite"
        aria-label="Chat messages"
      >
        <div className="mx-auto max-w-3xl">
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              onFeedback={msg.role === "assistant" ? handleFeedback : undefined}
            />
          ))}

          <TypingIndicator visible={isStreaming} toolStatus={toolStatus} />

          {showQuickReplies && messages.length <= 1 && (
            <QuickReplies visible onSelect={handleSend} />
          )}

          {/* Error retry bar */}
          {error && (
            <div className="my-2 flex items-center justify-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600" role="alert">
              <span>{error}</span>
              <button
                onClick={handleRetry}
                className="font-medium text-red-700 underline hover:text-red-800"
              >
                Retry
              </button>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Feedback toast */}
      {feedbackToast && (
        <div className="fixed bottom-20 left-1/2 -translate-x-1/2 rounded-lg bg-slate-800 px-4 py-2 text-sm text-white shadow-lg">
          Thanks for your feedback!
        </div>
      )}

      {/* Input */}
      <ChatInput
        onSend={handleSend}
        disabled={isStreaming}
        placeholder={isStreaming ? "Mia is responding..." : "Type your message..."}
      />
    </div>
  );
}
