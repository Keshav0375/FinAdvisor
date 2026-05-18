"use client";

import { useState } from "react";
import { StaleBadge } from "@/components/StaleBadge";
import type { Citation } from "@/hooks/useChat";

interface SourcesPanelProps {
  citations: Citation[];
}

export function SourcesPanel({ citations }: SourcesPanelProps) {
  const [expanded, setExpanded] = useState(false);

  if (citations.length === 0) return null;

  return (
    <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <svg
          className="h-3.5 w-3.5 flex-shrink-0 text-gray-500"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
        <span className="text-xs font-medium text-gray-600">
          {citations.length} source{citations.length !== 1 ? "s" : ""}
        </span>
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
        <div className="space-y-2 border-t border-gray-200 px-3 py-2">
          {citations.map((c) => (
            <div key={c.index} className="flex items-start gap-2">
              <span className="mt-0.5 flex h-5 min-w-[20px] items-center justify-center rounded bg-blue-100 text-[10px] font-bold text-blue-700">
                {c.index}
              </span>
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-xs font-medium text-gray-800">
                    {c.sourceTitle}
                  </span>
                  <StaleBadge lastReviewedAt={c.lastReviewedAt} />
                </div>
                {c.regulatoryRef && (
                  <span className="text-[11px] text-gray-500">
                    {c.regulatoryRef}
                  </span>
                )}
                <div className="text-[10px] text-gray-400">
                  Reviewed:{" "}
                  {new Date(c.lastReviewedAt).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                  })}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
