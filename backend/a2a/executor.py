"""A2A executor implementation for Veris research."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.protobuf.json_format import MessageToDict

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import Part, Task, TaskState, TaskStatus

from backend.a2a.config import VerisA2AAgent, VerisA2ALimits, veris_a2a_limits
from backend.a2a.service import run_veris_research

logger = logging.getLogger(__name__)

_EXECUTION_GATE: asyncio.Semaphore | None = None
_EXECUTION_GATE_LIMIT: int | None = None


def _structured_user_input(context: RequestContext) -> str:
    """Extract the documented A2A JSON input without accepting caller policy overrides."""
    text_prompt = context.get_user_input().strip()
    message = context.message
    if message is None:
        return text_prompt

    for part in message.parts:
        if part.WhichOneof("content") != "data":
            continue
        payload: Any = MessageToDict(part.data)
        if not isinstance(payload, dict):
            continue
        question = str(payload.get("question") or text_prompt).strip()
        identifiers = []
        for field in ("product_ids", "datasheet_ids"):
            values = payload.get(field)
            if not isinstance(values, list):
                continue
            normalized = [str(value).strip() for value in values if str(value).strip()]
            if normalized:
                identifiers.append(f"{field}: {', '.join(normalized[:50])}")
        if identifiers:
            return f"{question}\n\nPrioritize these identifiers when relevant:\n" + "\n".join(identifiers)
        return question
    return text_prompt


def _execution_gate(limits: VerisA2ALimits) -> asyncio.Semaphore:
    """Return the process-local cap for inbound A2A research work."""
    global _EXECUTION_GATE, _EXECUTION_GATE_LIMIT
    if _EXECUTION_GATE is None or _EXECUTION_GATE_LIMIT != limits.max_concurrent_requests:
        _EXECUTION_GATE = asyncio.Semaphore(limits.max_concurrent_requests)
        _EXECUTION_GATE_LIMIT = limits.max_concurrent_requests
    return _EXECUTION_GATE


class VerisResearchExecutor(AgentExecutor):
    """Converts a synchronous Veris RAG request into a completed A2A task."""

    def __init__(self, agent: VerisA2AAgent) -> None:
        self.agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            raise ValueError("A2A request context is missing a task or context ID")

        updater = TaskUpdater(event_queue, task_id, context_id)
        # A2A requires the initial Task event before status/artifact updates.
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        prompt = _structured_user_input(context)
        if not prompt:
            await updater.failed(
                updater.new_agent_message([Part(text="A non-empty text prompt is required.")])
            )
            return
        limits = veris_a2a_limits()
        if len(prompt) > limits.max_prompt_chars:
            await updater.failed(
                updater.new_agent_message(
                    [
                        Part(
                            text=(
                                "The request exceeds this agent's maximum input size "
                                f"of {limits.max_prompt_chars} characters."
                            )
                        )
                    ]
                )
            )
            return

        gate = _execution_gate(limits)
        await gate.acquire()
        release_gate = True
        await updater.start_work()
        work = asyncio.create_task(
            asyncio.to_thread(
                run_veris_research,
                prompt,
                request_id=task_id,
                limits=limits,
                domain=self.agent.domain,
            )
        )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(work),
                timeout=limits.timeout_seconds,
            )
            answer = str(result.get("answer") or "").strip()
            if not answer:
                raise RuntimeError("Veris research pipeline returned no answer")
            await updater.add_artifact(
                parts=[Part(text=answer)],
                name="veris-research-response",
                metadata={
                    "mime_type": "text/markdown",
                    "sources": result.get("sources") or [],
                },
            )
            await updater.complete()
        except TimeoutError:
            # The pipeline is synchronous, so it cannot be safely killed mid-call.
            # Keep its concurrency lease occupied until the worker exits.
            logger.warning("A2A Veris research task timed out task_id=%s", task_id)
            release_gate = False
            work.add_done_callback(lambda _completed: gate.release())
            await updater.failed(
                updater.new_agent_message(
                    [Part(text="Veris research exceeded the configured response deadline.")]
                )
            )
        except asyncio.CancelledError:
            release_gate = False
            work.add_done_callback(lambda _completed: gate.release())
            raise
        except Exception:
            logger.exception("A2A Veris research task failed task_id=%s", task_id)
            await updater.failed(
                updater.new_agent_message([Part(text="Veris research could not complete this request.")])
            )
        finally:
            if release_gate:
                gate.release()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id:
            return
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel()
