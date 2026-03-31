"use client";

interface QuickRepliesProps {
  visible: boolean;
  onSelect: (text: string) => void;
  replies?: string[];
}

const DEFAULT_REPLIES = [
  "Book an appointment",
  "Check my appointments",
  "I have a dental emergency",
  "Ask a question",
];

export default function QuickReplies({
  visible,
  onSelect,
  replies = DEFAULT_REPLIES,
}: QuickRepliesProps) {
  if (!visible) return null;

  return (
    <div className="mb-3 flex justify-start">
      <div className="max-w-[90%]">
        <div
          className="flex flex-wrap gap-2"
          role="group"
          aria-label="Suggested replies"
        >
          {replies.map((reply) => (
            <button
              key={reply}
              onClick={() => onSelect(reply)}
              className="rounded-full border border-teal-600 bg-white px-4 py-2 text-sm font-medium text-teal-600 transition hover:bg-teal-50 focus-visible:ring-2 focus-visible:ring-teal-600 focus-visible:ring-offset-2"
            >
              {reply}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
