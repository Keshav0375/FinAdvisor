"use client";

import { useUser } from "@/hooks/useUser";
import { USERS } from "@/lib/users";

export function UserSwitcher() {
  const { currentUser, setUser } = useUser();

  return (
    <div className="flex items-center gap-3">
      <select
        value={currentUser.sub}
        onChange={(e) => setUser(e.target.value)}
        className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
      >
        {USERS.map((user) => (
          <option key={user.sub} value={user.sub}>
            {user.name} — {user.tier} ({user.jurisdictions.join(", ")})
          </option>
        ))}
      </select>
      <span className="text-xs text-gray-400">
        Tier {currentUser.tierLevel} · {currentUser.licenses.join(", ")}
      </span>
    </div>
  );
}
