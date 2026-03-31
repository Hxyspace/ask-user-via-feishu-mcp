from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class DeliveryAskQueueState:
    delivery_key: str
    receive_id_type: str
    receive_id: str
    active_question_id: str = ""
    queued_question_ids: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not self.active_question_id and not self.queued_question_ids

    def to_target_queue_status(self) -> "TargetQueueStatus":
        return TargetQueueStatus(
            delivery_key=self.delivery_key,
            receive_id_type=self.receive_id_type,
            receive_id=self.receive_id,
            active_question_id=self.active_question_id,
            queued_question_ids=self.queued_question_ids,
        )


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


def activate_if_idle(
    queue_state: DeliveryAskQueueState,
    *,
    question_id: str,
) -> tuple[DeliveryAskQueueState, bool]:
    normalized_question_id = question_id.strip()
    if not normalized_question_id:
        raise ValueError("question_id must not be empty.")
    if normalized_question_id == queue_state.active_question_id:
        raise ValueError(f"question_id is already active: {normalized_question_id}")
    if normalized_question_id in queue_state.queued_question_ids:
        raise ValueError(f"question_id is already queued: {normalized_question_id}")
    if queue_state.active_question_id or queue_state.queued_question_ids:
        return queue_state, False
    return replace(queue_state, active_question_id=normalized_question_id), True


def enqueue_ask(
    queue_state: DeliveryAskQueueState,
    *,
    question_id: str,
) -> tuple[DeliveryAskQueueState, bool]:
    activated_state, activated = activate_if_idle(queue_state, question_id=question_id)
    if activated:
        return activated_state, True
    normalized_question_id = question_id.strip()
    return (
        replace(
            queue_state,
            queued_question_ids=queue_state.queued_question_ids + (normalized_question_id,),
        ),
        False,
    )


def promote_next_ask(queue_state: DeliveryAskQueueState) -> tuple[DeliveryAskQueueState, str]:
    if queue_state.active_question_id or not queue_state.queued_question_ids:
        return queue_state, ""
    next_question_id = queue_state.queued_question_ids[0]
    return (
        replace(
            queue_state,
            active_question_id=next_question_id,
            queued_question_ids=queue_state.queued_question_ids[1:],
        ),
        next_question_id,
    )


def remove_ask(
    queue_state: DeliveryAskQueueState,
    *,
    question_id: str,
) -> tuple[DeliveryAskQueueState, bool]:
    normalized_question_id = question_id.strip()
    if not normalized_question_id:
        raise ValueError("question_id must not be empty.")
    if normalized_question_id == queue_state.active_question_id:
        return replace(queue_state, active_question_id=""), True
    remaining_queued_question_ids = tuple(
        queued_question_id
        for queued_question_id in queue_state.queued_question_ids
        if queued_question_id != normalized_question_id
    )
    if remaining_queued_question_ids == queue_state.queued_question_ids:
        return queue_state, False
    return replace(queue_state, queued_question_ids=remaining_queued_question_ids), False
