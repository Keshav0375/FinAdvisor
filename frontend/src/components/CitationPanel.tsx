"use client";

import { StaleBadge } from "@/components/StaleBadge";
import type { Citation } from "@/hooks/useChat";

interface CitationPanelProps {
  citation: Citation;
  onClose: () => void;
}

export function CitationPanel({ citation, onClose }: CitationPanelProps) {
  return (
    <div className="animate-in fade-in slide-in-from-top-1 mt-1 rounded-lg border border-gray-200 bg-white p-3 shadow-lg duration-150">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 space-y-1.5">
          <div className="flex items-center gap-1.5">
            <span className="flex h-5 min-w-[20px] items-center justify-center rounded bg-blue-100 text-[10px] font-bold text-blue-700">
              {citation.index}
            </span>
            <span className="truncate text-xs font-medium text-gray-900">
              {citation.sourceTitle}
            </span>
            <StaleBadge lastReviewedAt={citation.lastReviewedAt} />
          </div>
          {citation.regulatoryRef && (
            <div className="flex items-center gap-1 text-[11px] text-gray-500">
              <svg
                className="h-3 w-3 flex-shrink-0"
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
              {citation.regulatoryRef}
            </div>
          )}
          <div className="text-[10px] text-gray-400">
            Reviewed:{" "}
            {new Date(citation.lastReviewedAt).toLocaleDateString("en-US", {
              year: "numeric",
              month: "short",
              day: "numeric",
            })}
          </div>
        </div>
        <button
          onClick={onClose}
          className="flex-shrink-0 rounded p-0.5 text-gray-300 transition-colors hover:bg-gray-100 hover:text-gray-500"
          aria-label="Close citation"
        >
          <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}
