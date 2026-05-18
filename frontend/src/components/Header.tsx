"use client";

import { UserSwitcher } from "@/components/UserSwitcher";

export function Header() {
  return (
    <header className="sticky top-0 z-20 border-b bg-white/95 px-4 py-3 backdrop-blur-sm">
      <div className="mx-auto flex max-w-5xl items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-sm font-bold text-white">
            FA
          </div>
          <div>
            <h1 className="text-base font-semibold leading-tight text-gray-900">
              FinAdvisor
            </h1>
            <p className="text-[10px] leading-tight text-gray-400">
              Compliance-Aware Wealth Advisor
            </p>
          </div>
        </div>
        <UserSwitcher />
      </div>
    </header>
  );
}
