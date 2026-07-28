"""Admin promotion CLI.

Set or clear the ``is_admin`` flag on a user identified by WhatsApp number.
Uses ``DATABASE_URL`` from the environment — locally that's your dev DB,
on Fly it's the production DB (reach it via ``fly ssh console -a wna-api``).

    python -m app.users.admin_cli promote +2348123456789
    python -m app.users.admin_cli demote +2348123456789
    python -m app.users.admin_cli list

Admin rights live on a database row, not in the code. Contributors who
clone the repo can only flip flags on their own local DB — they need
production DB access to affect the live site.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.users.models import User


async def _set_admin(wa_number: str, *, is_admin: bool) -> str:
    """Flip ``is_admin`` on the row matching ``wa_number``. Returns a
    human-readable status line."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            user = (
                await db.execute(select(User).where(User.wa_number == wa_number))
            ).scalar_one_or_none()
            if user is None:
                return f"No user with wa_number={wa_number}. They need to sign up first."
            if user.is_admin == is_admin:
                verb = "admin" if is_admin else "not admin"
                return f"{wa_number} is already {verb}. No change."
            user.is_admin = is_admin
            await db.commit()
            verb = "Promoted" if is_admin else "Demoted"
            return f"{verb} {wa_number}."
    finally:
        await engine.dispose()


async def _list_admins() -> str:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            rows = (
                (await db.execute(select(User).where(User.is_admin.is_(True))))
                .scalars()
                .all()
            )
            if not rows:
                return "No admins."
            return "\n".join(f"{u.wa_number}\t{u.name or ''}" for u in rows)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.users.admin_cli",
        description="Manage the is_admin flag on user rows.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd, help_text in (
        ("promote", "Grant admin rights to the given WhatsApp number."),
        ("demote", "Revoke admin rights from the given WhatsApp number."),
    ):
        p = sub.add_parser(cmd, help=help_text)
        p.add_argument("wa_number", help="E.164 number, e.g. +2348123456789")
    sub.add_parser("list", help="Show every current admin.")

    args = parser.parse_args(argv)
    if args.cmd == "list":
        print(asyncio.run(_list_admins()))
    else:
        print(asyncio.run(_set_admin(args.wa_number, is_admin=args.cmd == "promote")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
