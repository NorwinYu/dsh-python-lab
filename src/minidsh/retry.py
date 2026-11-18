import os
import random
from dataclasses import dataclass
from typing import Optional, Tuple


DEFAULT_MAX_RETRIES = 5
DEFAULT_INITIAL_DELAY_MS = 500
DEFAULT_MAX_DELAY_MS = 10_000
DEFAULT_JITTER_RATIO = 0.1
DEFAULT_RETRYABLE_CODES = ("EMPTY_RESPONSE", "RATE_LIMIT", "SERVER", "TIMEOUT", "TRANSPORT")


@dataclass(frozen=True)
class RetryPolicy:
    mode: str = "normal"
    max_retries: int = DEFAULT_MAX_RETRIES
    retryable_codes: Tuple[str, ...] = DEFAULT_RETRYABLE_CODES
    initial_delay_ms: int = DEFAULT_INITIAL_DELAY_MS
    max_delay_ms: int = DEFAULT_MAX_DELAY_MS
    jitter_ratio: float = DEFAULT_JITTER_RATIO

    @classmethod
    def from_env(cls, enabled: bool = True) -> "RetryPolicy":
        try:
            max_retries = int(os.environ.get("DEEPSEEK_RETRY_MAX", str(DEFAULT_MAX_RETRIES)))
            initial_delay = int(os.environ.get("DEEPSEEK_RETRY_INITIAL_DELAY_MS", str(DEFAULT_INITIAL_DELAY_MS)))
            max_delay = int(os.environ.get("DEEPSEEK_RETRY_MAX_DELAY_MS", str(DEFAULT_MAX_DELAY_MS)))
            jitter = float(os.environ.get("DEEPSEEK_RETRY_JITTER_RATIO", str(DEFAULT_JITTER_RATIO)))
        except ValueError as exc:
            raise ValueError("invalid DeepSeek retry numeric configuration") from exc
        mode = os.environ.get("DEEPSEEK_RETRY_MODE", "normal")
        if mode not in {"normal", "always"}:
            raise ValueError("DEEPSEEK_RETRY_MODE must be normal or always")
        if max_retries < 0 or initial_delay <= 0 or max_delay <= 0 or initial_delay > max_delay:
            raise ValueError("invalid DeepSeek retry count or backoff delay configuration")
        if not 0 <= jitter <= 1:
            raise ValueError("DEEPSEEK_RETRY_JITTER_RATIO must be between 0 and 1")
        if not enabled:
            max_retries = 0
            mode = "normal"
        return cls(mode, max_retries, DEFAULT_RETRYABLE_CODES, initial_delay, max_delay, jitter)

    def allows(self, code: str, retries: int) -> bool:
        if self.mode == "always":
            return True
        return retries < self.max_retries and code in self.retryable_codes

    def delay_ms(self, retries: int, provider_retry_after_ms: Optional[int] = None) -> int:
        local = min(self.initial_delay_ms * (2 ** retries), self.max_delay_ms)
        if provider_retry_after_ms is not None and provider_retry_after_ms > 0:
            local = min(provider_retry_after_ms, self.max_delay_ms)
        multiplier = 1 + random.uniform(-self.jitter_ratio, self.jitter_ratio)
        return max(1, round(local * multiplier))
