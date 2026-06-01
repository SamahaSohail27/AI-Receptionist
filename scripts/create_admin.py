"""
Create the first admin user.

Usage (run from project root, after `alembic upgrade head`):
    python scripts/create_admin.py
    python scripts/create_admin.py --email admin@clinic.pk --name "Clinic Admin"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


async def create_admin(email: str, name: str, password: str) -> None:
    from sqlalchemy import select
    from core.database import AsyncSessionLocal
    from models.auth import User
    from core.auth import hash_password

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing:
            print(f"User {email} already exists (role={existing.role}). Skipping.")
            return

        user = User(
            name=name,
            email=email,
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"Admin created — id={user.id}  email={email}")
        print("IMPORTANT: log in and change the password immediately.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create first admin user")
    parser.add_argument("--email", default="admin@clinic.pk")
    parser.add_argument("--name", default="Clinic Admin")
    parser.add_argument("--password", default="Change-Me-Now-123!")
    args = parser.parse_args()

    asyncio.run(create_admin(args.email, args.name, args.password))


if __name__ == "__main__":
    main()
