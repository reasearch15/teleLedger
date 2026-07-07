from __future__ import annotations

import asyncio
import getpass
from collections.abc import Callable
from dataclasses import dataclass

from app.auth.security import normalize_username, validate_new_password
from app.db.session import SessionFactory
from app.services.user import AuthService, UsernameAlreadyExistsError


@dataclass(frozen=True, slots=True)
class AdminCredentials:
    """Validated values collected by the interactive admin CLI."""

    username: str
    password: str


def prompt_admin_credentials(
    input_function: Callable[[str], str] = input,
    password_function: Callable[[str], str] = getpass.getpass,
) -> AdminCredentials:
    """Prompt for and validate initial administrator credentials."""
    username = normalize_username(input_function("Admin username: "))
    password = password_function("Admin password: ")
    confirmation = password_function("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    return AdminCredentials(
        username=username,
        password=validate_new_password(password),
    )


async def create_admin(credentials: AdminCredentials) -> str:
    """Persist one administrator and return its normalized username."""
    async with SessionFactory() as session:
        user = await AuthService(session).create_admin(
            credentials.username,
            credentials.password,
        )
        return user.username


def main() -> None:
    """Interactive CLI entry point."""
    try:
        credentials = prompt_admin_credentials()
        username = asyncio.run(create_admin(credentials))
    except (ValueError, UsernameAlreadyExistsError) as error:
        raise SystemExit(str(error)) from error
    print(f"Administrator '{username}' created.")


if __name__ == "__main__":
    main()
