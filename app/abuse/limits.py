"""Rate limiting, daily caps, cooldowns, and blocklist.

Backed by Redis counters keyed on user_id with windowed TTLs.
"""


async def check_rate_limit(user_id: str) -> bool:
    raise NotImplementedError


async def check_identical_query_cooldown(user_id: str, query: str) -> bool:
    raise NotImplementedError


async def is_blocked(user_id: str) -> bool:
    raise NotImplementedError
