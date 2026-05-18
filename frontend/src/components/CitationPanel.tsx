"use client";

import { StaleBadge } from "@/components/StaleBadge";
import type { Citation } from "@/hooks/useChat";

interface CitationPanelProps {
  citation: Citation;
  onClose: () => void;
}

export function CitationPanel({ citation, onClose }: CitationPanelProps) {
  return (
    <div className="mt-2 rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-1">
            <span className="font-medium text-gray-900">
              [{citation.index}] {citation.sourceTitle}
            </span>
            <StaleBadge lastReviewedAt={citation.lastReviewedAt} />
          </div>
          {citation.regulatoryRef && (
            <div className="text-gray-500">{citation.regulatoryRef}</div>
          )}
          <div className="text-gray-400">
            Reviewed: {new Date(citation.lastReviewedAt).toLocaleDateString()}
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600"
          aria-label="Close citation"
        >
          &times;
        </button>
      </div>
    </div>
  );
}
