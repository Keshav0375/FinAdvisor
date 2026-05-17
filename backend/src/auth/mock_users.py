from __future__ import annotations

from typing import Any

MOCK_USERS: dict[str, dict[str, Any]] = {
    "sarah_chen": {
        "sub": "sarah_chen",
        "name": "Sarah Chen",
        "tier": "senior",
        "tier_level": 3,
        "jurisdictions": ["US"],
        "licenses": ["Series-7", "Series-66"],
    },
    "alex_kim": {
        "sub": "alex_kim",
        "name": "Alex Kim",
        "tier": "associate",
        "tier_level": 1,
        "jurisdictions": ["EU"],
        "licenses": ["MiFID-II"],
    },
    "james_wright": {
        "sub": "james_wright",
        "name": "James Wright",
        "tier": "private_wealth",
        "tier_level": 4,
        "jurisdictions": ["UK"],
        "licenses": ["FCA"],
    },
    "priya_sharma": {
        "sub": "priya_sharma",
        "name": "Priya Sharma",
        "tier": "advisor",
        "tier_level": 2,
        "jurisdictions": ["US", "EU"],
        "licenses": ["Series-7", "MiFID-II"],
    },
}
