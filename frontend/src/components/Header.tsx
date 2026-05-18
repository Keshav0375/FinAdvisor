"use client";

import { UserSwitcher } from "@/components/UserSwitcher";

export function Header() {
  return (
    <header className="border-b border-gray-200 bg-white px-4 py-3">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900">FinAdvisor</h1>
        <UserSwitcher />
      </div>
    </header>
  );
}
