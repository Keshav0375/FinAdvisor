export interface UserProfile {
  sub: string;
  name: string;
  tier: string;
  tierLevel: number;
  jurisdictions: string[];
  licenses: string[];
}

export const USERS: UserProfile[] = [
  {
    sub: "sarah_chen",
    name: "Sarah Chen",
    tier: "senior",
    tierLevel: 3,
    jurisdictions: ["US"],
    licenses: ["Series-7", "Series-66"],
  },
  {
    sub: "alex_kim",
    name: "Alex Kim",
    tier: "associate",
    tierLevel: 1,
    jurisdictions: ["EU"],
    licenses: ["MiFID-II"],
  },
  {
    sub: "james_wright",
    name: "James Wright",
    tier: "private_wealth",
    tierLevel: 4,
    jurisdictions: ["UK"],
    licenses: ["FCA"],
  },
  {
    sub: "priya_sharma",
    name: "Priya Sharma",
    tier: "advisor",
    tierLevel: 2,
    jurisdictions: ["US", "EU"],
    licenses: ["Series-7", "MiFID-II"],
  },
];

export const DEFAULT_USER = USERS[0];
