"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { MessageBubble } from "@/components/MessageBubble";
import { useChat } from "@/hooks/useChat";

export function ChatWindow() {
  const { messages, sendMessage, isStreaming, clearMessages } = useChat();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    sendMessage(input.trim());
    setInput("");
  }

  return (
    <div className="flex h-[calc(100vh-65px)] flex-col">
      <div className="scrollbar-thin flex-1 space-y-3 overflow-y-auto px-4 py-6 sm:px-6">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-blue-50">
              <svg
                className="h-6 w-6 text-blue-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z"
                />
              </svg>
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-gray-600">
                Wealth Advisory Assistant
              </p>
              <p className="mt-1 max-w-sm text-xs text-gray-400">
                Ask about products, suitability rules, or compliance. All
                responses include source citations.
              </p>
            </div>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      <form
        onSubmit={handleSubmit}
        className="border-t bg-white px-4 py-3 sm:px-6"
      >
        <div className="mx-auto flex max-w-3xl gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about products, suitability, or compliance..."
            disabled={isStreaming}
            className="flex-1 rounded-lg border border-gray-200 px-4 py-2.5 text-sm transition-colors placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
          />
          <button
            type="submit"
            disabled={isStreaming || !input.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none"
          >
            {isStreaming ? (
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
                <span className="hidden sm:inline">Thinking</span>
              </span>
            ) : (
              "Send"
            )}
          </button>
          {messages.length > 0 && !isStreaming && (
            <button
              type="button"
              onClick={clearMessages}
              className="rounded-lg border border-gray-200 px-3 py-2.5 text-sm text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-700"
            >
              Clear
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
