import { create } from "zustand";
import { DEFAULT_USER, USERS, type UserProfile } from "@/lib/users";

interface UserState {
  currentUser: UserProfile;
  setUser: (sub: string) => void;
}

function loadPersistedUser(): UserProfile {
  if (typeof window === "undefined") return DEFAULT_USER;
  const stored = localStorage.getItem("finadvisor_user");
  if (!stored) return DEFAULT_USER;
  const found = USERS.find((u) => u.sub === stored);
  return found ?? DEFAULT_USER;
}

export const useUser = create<UserState>((set) => ({
  currentUser: loadPersistedUser(),
  setUser: (sub: string) => {
    const user = USERS.find((u) => u.sub === sub);
    if (!user) return;
    localStorage.setItem("finadvisor_user", sub);
    set({ currentUser: user });
  },
}));
