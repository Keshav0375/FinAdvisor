"use client";

interface StaleBadgeProps {
  lastReviewedAt: string;
}

export function StaleBadge({ lastReviewedAt }: StaleBadgeProps) {
  const reviewed = new Date(lastReviewedAt);
  const monthsAgo =
    (Date.now() - reviewed.getTime()) / (1000 * 60 * 60 * 24 * 30);

  if (monthsAgo <= 12) return null;

  return (
    <span className="ml-1 inline-flex items-center rounded bg-orange-100 px-1.5 py-0.5 text-[10px] font-semibold text-orange-700">
      STALE
    </span>
  );
}
