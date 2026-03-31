from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TargetQueueStatus:
    delivery_key: str
    receive_id_type: str
    receive_id: str
    active_question_id: str
    queued_question_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_key": self.delivery_key,
            "receive_id_type": self.receive_id_type,
            "receive_id": self.receive_id,
            "active_question_id": self.active_question_id,
            "queued_question_ids": list(self.queued_question_ids),
        }


@dataclass(frozen=True)
class AskStatusSnapshot:
    active_ask_count: int
    queued_ask_count: int
    queues_by_target: tuple[TargetQueueStatus, ...] = ()
    queue_exempt_question_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_ask_count": self.active_ask_count,
            "queued_ask_count": self.queued_ask_count,
            "queues_by_target": [queue.to_dict() for queue in self.queues_by_target],
            "queue_exempt_question_ids": list(self.queue_exempt_question_ids),
        }
