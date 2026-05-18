"use client";

import { CitationInline } from "@/components/CitationInline";
import type { Message } from "@/hooks/useChat";
import { parseCitations } from "@/lib/parseCitations";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const segments = parseCitations(message.content);
  const citationMap = new Map(message.citations.map((c) => [c.index, c]));

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
        {message.toolCalls.length > 0 && (
          <div className="mb-2 space-y-1 border-b border-gray-100 pb-2">
            {message.toolCalls.map((tc) => (
              <div
                key={tc.toolUseId}
                className="flex items-center gap-1.5 text-[11px] text-gray-400"
              >
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-green-400" />
                <span className="font-mono">{tc.tool}</span>
              </div>
            ))}
          </div>
        )}

        {message.error ? (
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-red-400">!</span>
            <p className="text-sm text-red-600">{message.error}</p>
          </div>
        ) : (
          <div className="whitespace-pre-wrap text-sm leading-relaxed">
            {isUser ? (
              message.content
            ) : (
              <>
                {segments.map((seg, i) =>
                  seg.type === "text" ? (
                    <span key={i}>{seg.content}</span>
                  ) : (
                    <CitationInline
                      key={i}
                      index={seg.index}
                      citation={citationMap.get(seg.index)}
                    />
                  )
                )}
                {message.isStreaming && (
                  <span className="ml-0.5 inline-block h-4 w-1 animate-pulse rounded-full bg-blue-400" />
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
