"use client";

interface TypingIndicatorProps {
  visible: boolean;
  toolStatus?: string;
}

export default function TypingIndicator({ visible, toolStatus }: TypingIndicatorProps) {
  if (!visible) return null;

  return (
    <div
      className="mb-3 flex justify-start"
      aria-live="polite"
      aria-label={toolStatus || "Mia is typing"}
      role="status"
    >
      <div className="max-w-[80%]">
        <span className="mb-1 block text-sm font-medium text-teal-600">
          Mia
        </span>
        <div className="rounded-2xl bg-white px-4 py-3 shadow-sm">
          {toolStatus ? (
            <div className="flex items-center gap-2">
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-teal-400 border-t-transparent" />
              <span className="text-sm italic text-slate-500">{toolStatus}</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5" aria-hidden="true">
              <span
                className="animate-bounce-dot h-2 w-2 rounded-full bg-teal-400"
                style={{ animationDelay: "0ms" }}
              />
              <span
                className="animate-bounce-dot h-2 w-2 rounded-full bg-teal-400"
                style={{ animationDelay: "200ms" }}
              />
              <span
                className="animate-bounce-dot h-2 w-2 rounded-full bg-teal-400"
                style={{ animationDelay: "400ms" }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
