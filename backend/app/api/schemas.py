"""Request bodies. Strict: unknown fields are rejected and every string has a max length."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,31}$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginBody(Strict):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordBody(Strict):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)

    # passwords are not stripped
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class UserCreateBody(Strict):
    username: str = Field(min_length=2, max_length=32)
    display_name: str = Field(min_length=1, max_length=120)
    title: str = Field(default="", max_length=120)
    initials: str = Field(default="", max_length=4)
    email: str | None = Field(default=None, max_length=254)
    role: Literal["owner", "desk", "nurse"]
    is_admin: bool = False

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.lower()
        if not USERNAME_RE.match(v):
            raise ValueError("username must be 2 to 32 characters: letters, digits, dot, dash or underscore, starting with a letter")
        return v


class UserPatchBody(Strict):
    role: Literal["owner", "desk", "nurse"] | None = None
    is_admin: bool | None = None
    active: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    initials: str | None = Field(default=None, max_length=4)


class SettingsPutBody(Strict):
    settings: dict[str, object] = Field(max_length=40)


class DraftPatchBody(Strict):
    body: str = Field(min_length=1, max_length=2000)


class AdvanceBody(Strict):
    # Optional guard: the stage the client saw. A mismatch returns 409.
    from_stage: int | None = Field(default=None, ge=0, le=2)
