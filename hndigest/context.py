from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from openai import AsyncOpenAI

from .config import Config
from .hn import HNClient
from .storage import Cache

@dataclass
class Ctx:
    hn: HNClient
    http: httpx.AsyncClient
    client: AsyncOpenAI
    cfg: Config
    sem: asyncio.Semaphore
    usage: dict
    cache: Cache
    knowledge_profile: Optional[str] = None

