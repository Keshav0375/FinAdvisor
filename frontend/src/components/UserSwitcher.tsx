"use client";

import { useUser } from "@/hooks/useUser";
import { USERS } from "@/lib/users";

export function UserSwitcher() {
  const { currentUser, setUser } = useUser();

  return (
    <div className="flex items-center gap-2">
      <select
        value={currentUser.sub}
        onChange={(e) => setUser(e.target.value)}
        className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-700 shadow-sm transition-colors hover:border-gray-300 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 sm:text-sm sm:px-3"
      >
        {USERS.map((user) => (
          <option key={user.sub} value={user.sub}>
            {user.name} — {user.tier}
          </option>
        ))}
      </select>
      <div className="hidden items-center gap-1.5 sm:flex">
        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
          T{currentUser.tierLevel}
        </span>
        <span className="text-[10px] text-gray-400">
          {currentUser.jurisdictions.join("/")}
        </span>
      </div>
    </div>
  );
}
