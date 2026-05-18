"use client";

import { useState } from "react";
import type { ToolCall } from "@/hooks/useChat";

interface ToolCallCardProps {
  toolCall: ToolCall;
}

export function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = toolCall.status === "running";

  return (
    <div
      className={`rounded-lg border px-3 py-2 transition-colors duration-300 ${
        isRunning
          ? "border-amber-200 bg-amber-50"
          : "border-gray-200 bg-gray-50"
      }`}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 text-left"
      >
        {isRunning ? (
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
        ) : (
          <span className="inline-block h-2 w-2 rounded-full bg-green-400" />
        )}
        <span className="font-mono text-xs font-medium text-gray-700">
          {toolCall.tool}
        </span>
        {isRunning ? (
          <span className="text-[10px] text-amber-600">Running...</span>
        ) : (
          toolCall.durationMs != null && (
            <span className="text-[10px] text-gray-400">
              {(toolCall.durationMs / 1000).toFixed(1)}s
            </span>
          )
        )}
        <svg
          className={`ml-auto h-3.5 w-3.5 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>
      {expanded && (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-gray-100 p-2 text-[11px] text-gray-600">
          {JSON.stringify(toolCall.input, null, 2)}
        </pre>
      )}
    </div>
  );
}
