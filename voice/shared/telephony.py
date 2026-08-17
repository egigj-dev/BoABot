"""Call-control boundary for both schemas.

Real Twilio, Azure Communication Services, or SIP media and transfer adapters are
deployment work. The included simulator is fail-closed and offline-testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class CallControl(ABC):
    @abstractmethod
    async def answer(self, call_id: str) -> bool: ...

    @abstractmethod
    async def hangup(self, call_id: str) -> bool: ...

    @abstractmethod
    async def transfer(self, call_id: str, reason: str) -> bool: ...

    @abstractmethod
    async def agent_accepted(self, call_id: str) -> bool: ...


@dataclass(slots=True)
class SimulatedCallControl(CallControl):
    accept_transfers: bool = True
    answered: bool = False
    hung_up: bool = False
    transfer_requested: bool = False
    accepted: bool = False

    async def answer(self, call_id: str) -> bool:
        self.answered = bool(call_id)
        return self.answered

    async def hangup(self, call_id: str) -> bool:
        self.hung_up = bool(call_id)
        return self.hung_up

    async def transfer(self, call_id: str, reason: str) -> bool:
        self.transfer_requested = bool(call_id and reason)
        self.accepted = self.transfer_requested and self.accept_transfers
        return self.accepted

    async def agent_accepted(self, call_id: str) -> bool:
        return bool(call_id) and self.accepted
