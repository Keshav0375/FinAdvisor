"use client";

import { StreamingText } from "@/components/StreamingText";
import type { Message } from "@/hooks/useChat";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-3 ${
          isUser
            ? "bg-blue-600 text-white"
            : "border border-gray-200 bg-white text-gray-900"
        }`}
      >
        {message.toolCalls.length > 0 && (
          <div className="mb-2 space-y-1">
            {message.toolCalls.map((tc) => (
              <div
                key={tc.toolUseId}
                className="flex items-center gap-1.5 text-xs text-gray-400"
              >
                <span className="inline-block h-1.5 w-1.5 rounded-full bg-green-400" />
                {tc.tool}
              </div>
            ))}
          </div>
        )}

        {message.error ? (
          <p className="text-sm text-red-500">{message.error}</p>
        ) : (
          <div className="whitespace-pre-wrap text-sm leading-relaxed">
            <StreamingText
              content={message.content}
              isStreaming={message.isStreaming}
            />
          </div>
        )}
      </div>
    </div>
  );
}
