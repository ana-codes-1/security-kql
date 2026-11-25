import math
import threading
import time
from collections import deque
from contextlib import contextmanager

from fastapi import HTTPException


class PublicRequestBudget:
    def __init__(self, limit=30, period=3600, concurrent=2, clock=time.monotonic):
        self.limit = limit
        self.period = period
        self.concurrent = concurrent
        self.clock = clock
        self.requests = deque()
        self.active = 0
        self.lock = threading.Lock()

    @contextmanager
    def slot(self):
        with self.lock:
            now = self.clock()
            while self.requests and self.requests[0] <= now - self.period:
                self.requests.popleft()
            if len(self.requests) >= self.limit:
                retry = max(1, math.ceil(self.requests[0] + self.period - now))
                raise HTTPException(
                    429,
                    "This public demo's shared limit is 30 requests per hour. Please try later.",
                    headers={"Retry-After": str(retry)},
                )
            if self.active >= self.concurrent:
                raise HTTPException(
                    429, "The demo is busy. Please retry shortly.", headers={"Retry-After": "5"}
                )
            self.requests.append(now)
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
