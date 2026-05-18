"use client";

import { MarkdownContent } from "@/components/MarkdownContent";
import { SourcesPanel } from "@/components/SourcesPanel";
import { ToolCallCard } from "@/components/ToolCallCard";
import type { Message } from "@/hooks/useChat";

interface MessageBubbleProps {
  message: Message;
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-1.5 py-1">
      <span
        className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
        style={{ animationDelay: "0ms" }}
      />
      <span
        className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
        style={{ animationDelay: "150ms" }}
      />
      <span
        className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
        style={{ animationDelay: "300ms" }}
      />
    </div>
  );
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  const allToolsDone =
    message.toolCalls.length > 0 &&
    message.toolCalls.every((tc) => tc.status === "done");
  const showThinking =
    message.isStreaming &&
    !message.content &&
    (message.toolCalls.length === 0 || allToolsDone);

  return (
    <div
      className={`flex ${isUser ? "justify-end" : "justify-start"} animate-in fade-in slide-in-from-bottom-2 duration-200`}
    >
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 shadow-sm sm:max-w-[75%] ${
          isUser
            ? "bg-blue-600 text-white"
            : "border border-gray-100 bg-white text-gray-800"
        }`}
      >
        {!isUser && message.toolCalls.length > 0 && (
          <div className="mb-3 space-y-1.5">
            {message.toolCalls.map((tc) => (
              <ToolCallCard key={tc.toolUseId} toolCall={tc} />
            ))}
          </div>
        )}

        {showThinking && <ThinkingIndicator />}

        {message.error ? (
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-red-400">!</span>
            <p className="text-sm text-red-600">{message.error}</p>
          </div>
        ) : (
          <>
            <div className="text-sm leading-relaxed">
              {isUser ? (
                <p className="whitespace-pre-wrap">{message.content}</p>
              ) : (
                <>
                  {message.content && (
                    <MarkdownContent
                      content={message.content}
                      citations={message.citations}
                    />
                  )}
                  {message.isStreaming && message.content && (
                    <span className="ml-0.5 inline-block h-4 w-1 animate-pulse rounded-full bg-blue-400" />
                  )}
                </>
              )}
            </div>
            {!isUser && !message.isStreaming && (
              <SourcesPanel citations={message.citations} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
