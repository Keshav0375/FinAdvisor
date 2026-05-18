"use client";

import { useState } from "react";
import { CitationPanel } from "@/components/CitationPanel";
import type { Citation } from "@/hooks/useChat";

interface CitationInlineProps {
  index: number;
  citation: Citation | undefined;
}

export function CitationInline({ index, citation }: CitationInlineProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <span className="relative inline-block">
      <button
        onClick={() => citation && setExpanded(!expanded)}
        className={`mx-0.5 inline-flex h-5 min-w-[20px] items-center justify-center rounded text-[11px] font-semibold ${
          citation
            ? "cursor-pointer bg-blue-100 text-blue-700 hover:bg-blue-200"
            : "cursor-default bg-gray-100 text-gray-400"
        }`}
      >
        {index}
      </button>
      {expanded && citation && (
        <div className="absolute left-0 top-6 z-10 w-72">
          <CitationPanel
            citation={citation}
            onClose={() => setExpanded(false)}
          />
        </div>
      )}
    </span>
  );
}
