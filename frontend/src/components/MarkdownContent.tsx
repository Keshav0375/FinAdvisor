"use client";

import ReactMarkdown from "react-markdown";
import { CitationInline } from "@/components/CitationInline";
import type { Citation } from "@/hooks/useChat";

interface MarkdownContentProps {
  content: string;
  citations: Citation[];
}

const CITATION_SPLIT = /(\[\d+\])/g;

function renderWithCitations(
  text: string,
  citationMap: Map<number, Citation>
) {
  const parts = text.split(CITATION_SPLIT);
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      const idx = parseInt(match[1], 10);
      return (
        <CitationInline key={i} index={idx} citation={citationMap.get(idx)} />
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export function MarkdownContent({ content, citations }: MarkdownContentProps) {
  const citationMap = new Map(citations.map((c) => [c.index, c]));

  return (
    <ReactMarkdown
      components={{
        h1: ({ children }) => (
          <h1 className="mb-2 mt-3 text-base font-bold text-gray-900 first:mt-0">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="mb-1.5 mt-3 text-sm font-bold text-gray-900 first:mt-0">
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="mb-1 mt-2 text-sm font-semibold text-gray-800 first:mt-0">
            {children}
          </h3>
        ),
        p: ({ children }) => {
          if (typeof children === "string") {
            return (
              <p className="mb-2 last:mb-0">
                {renderWithCitations(children, citationMap)}
              </p>
            );
          }
          return <p className="mb-2 last:mb-0">{children}</p>;
        },
        strong: ({ children }) => (
          <strong className="font-semibold text-gray-900">{children}</strong>
        ),
        ul: ({ children }) => (
          <ul className="mb-2 ml-4 list-disc space-y-0.5">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="mb-2 ml-4 list-decimal space-y-0.5">{children}</ol>
        ),
        li: ({ children }) => {
          if (typeof children === "string") {
            return (
              <li>{renderWithCitations(children, citationMap)}</li>
            );
          }
          return <li>{children}</li>;
        },
        hr: () => <hr className="my-3 border-gray-200" />,
        code: ({ children }) => (
          <code className="rounded bg-gray-100 px-1 py-0.5 text-[12px] text-gray-700">
            {children}
          </code>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
