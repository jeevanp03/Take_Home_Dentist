"use client";

import { useEffect, useState } from "react";

export default function Home() {
  const [health, setHealth] = useState<string>("checking...");

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    fetch(`${apiUrl}/api/health`)
      .then((res) => res.json())
      .then((data) => setHealth(data.status))
      .catch(() => setHealth("unreachable"));
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex flex-col items-center gap-6 text-center">
        <h1 className="text-3xl font-semibold tracking-tight text-black dark:text-zinc-50">
          Dental Practice Chatbot
        </h1>
        <p className="text-lg text-zinc-600 dark:text-zinc-400">
          Backend status:{" "}
          <span
            className={
              health === "ok"
                ? "font-medium text-green-600"
                : "font-medium text-red-500"
            }
          >
            {health}
          </span>
        </p>
      </main>
    </div>
  );
}
