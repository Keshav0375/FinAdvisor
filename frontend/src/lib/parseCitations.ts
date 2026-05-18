export interface TextSegment {
  type: "text";
  content: string;
}

export interface CitationSegment {
  type: "citation";
  index: number;
}

export type Segment = TextSegment | CitationSegment;

const CITATION_PATTERN = /\[(\d+)\]/g;

export function parseCitations(text: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = CITATION_PATTERN.exec(text)) !== null) {
    const matchStart = match.index;
    if (matchStart > lastIndex) {
      segments.push({ type: "text", content: text.slice(lastIndex, matchStart) });
    }
    segments.push({ type: "citation", index: parseInt(match[1], 10) });
    lastIndex = matchStart + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push({ type: "text", content: text.slice(lastIndex) });
  }

  return segments;
}
