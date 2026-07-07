"""Runtime context shared across one digest run.

Summary:
    Groups long-lived clients, config, semaphores, usage counters, cache, and
    the loaded reader knowledge profile so pipeline functions can pass one
    object instead of many parameters.

Adding functions:
    Usually do not add behavior here. Add fields only when multiple pipeline
    stages need the same runtime state; put logic in the stage module that owns
    the behavior.
"""

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
