#
# Copyright IBM Corp. 2026
# SPDX-License-Identifier: Apache-2.0
#

"""Abstract agent interface and shared data models.

Every agent backend subclasses ``AbstractAgent`` and implements ``run()``,
``stream()``, ``reflect()``, and ``close()``. The runner (``runner.py``)
composes instances according to a chosen pattern — the agent itself has no
pattern awareness.

Skill files are Markdown documents with a YAML front-matter block::

    ---
    name: concise_answers
    description: Prefer short, direct answers with a worked example.
    tags:
      - qa
    ---

    ## Concise answers

    - Lead with the answer, then the reasoning.
    - Include one concrete example.
    ...

Skills are loaded with ``load_skill()`` and attached to ``AgentConfig.skills``.
The agent concatenates their ``prompt_fragment`` values into the system prompt
before each run via ``_build_system_prompt()``. A skill with no ``tags`` applies
to every agent; otherwise it activates only when its tags overlap the agent's.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# Backend-neutral defaults. Override per instance via ``AgentConfig``.
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_RETRIES = 5
# Retries the underlying HTTP client makes on transient (timeout/429/5xx) errors.
DEFAULT_HTTP_RETRIES = 3

# Injected by ``reflect()`` so an agent critiques and improves its own output.
DEFAULT_REFLECT_PROMPT = (
    "Review your previous response for correctness, completeness, and clarity. "
    "Identify any mistakes or gaps, then produce an improved final response."
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(str, Enum):
    """Role of a participant in a conversation turn.

    Values:
        USER: Message from the human or orchestrating layer.
        ASSISTANT: Message produced by the agent.
        TOOL: Result returned by a tool invocation.
    """

    USER      = "user"
    ASSISTANT = "assistant"
    TOOL      = "tool"


class StopReason(str, Enum):
    """Why an agent run ended.

    Values:
        END_TURN: Agent produced a final response naturally.
        TOOL_USE: Agent paused mid-turn to invoke a tool.
        MAX_TOKENS: Output token limit was reached before completion.
        ERROR: Run terminated due to an unrecoverable error.
    """

    END_TURN   = "end_turn"
    TOOL_USE   = "tool_use"
    MAX_TOKENS = "max_tokens"
    ERROR      = "error"


# ---------------------------------------------------------------------------
# Message and tool models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """A single turn in a conversation thread.

    Attributes:
        role: Who produced this message.
        content: Text content of the message.
        tool_call_id: ID of the tool call this message responds to.
            Populated only when ``role`` is ``TOOL``.
        tool_name: Name of the tool that produced this message.
            Populated only when ``role`` is ``TOOL``.
        tool_calls: Tool invocations requested by this message. Populated only
            when ``role`` is ``ASSISTANT`` and the turn requested tools; kept so
            a message list can round-trip without dropping the ``tool_use``
            blocks that later ``TOOL`` messages answer (an orphaned
            ``tool_result`` is a hard API error).
    """

    role: Role
    content: str
    tool_call_id: str = ''
    tool_name: str = ''
    tool_calls: list['ToolCall'] = Field(default_factory=list)


class ToolCall(BaseModel):
    """Records a single tool invocation made by the agent during a run.

    Attributes:
        id: Unique identifier assigned by the backend for this call.
        name: Name of the tool that was invoked.
        arguments: Key-value arguments the agent passed to the tool.
        result: String result returned by the tool. Empty when the result was
            not captured (e.g. an older run) — recorded so the agent's trajectory
            can be inspected without re-running.
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ''


# ``Message.tool_calls`` forward-references ``ToolCall`` (defined just above);
# resolve it now that both models exist.
Message.model_rebuild()


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------

class Skill(BaseModel):
    """A reusable behavior loaded from a Markdown file and injected into an
    agent's system prompt at run time.

    Attributes:
        name: Unique identifier, e.g. ``concise_answers``.
        description: One-line summary explaining when this skill is active.
        prompt_fragment: Markdown content injected verbatim into the system prompt.
        tags: Free-form labels this skill applies to. An empty list means the
            skill applies to every agent regardless of tags.
    """

    name: str
    description: str
    prompt_fragment: str
    tags: list[str] = []


def load_skill(path: Path | str) -> Skill:
    """Load a ``Skill`` from a Markdown file with YAML front-matter.

    The file must begin with a ``---``-delimited front-matter block containing
    at least ``name`` and ``description``. Everything after the closing ``---``
    becomes the ``prompt_fragment``.

    Args:
        path: Path to the ``.md`` skill file.

    Returns:
        A populated ``Skill`` instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file has no front-matter or is missing required fields.

    Example:
        >>> skill = load_skill("skills/concise_answers.md")
        >>> print(skill.name)
        concise_answers
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")

    if not raw.startswith("---"):
        raise ValueError(f"Skill file '{path}' has no YAML front-matter block.")

    parts = raw.split("---", maxsplit=2)
    if len(parts) < 3:
        raise ValueError(f"Skill file '{path}' front-matter block is not closed.")

    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ValueError(f"Skill file '{path}' has invalid front-matter: {exc}") from exc

    for required in ("name", "description"):
        if required not in meta:
            raise ValueError(
                f"Skill file '{path}' is missing required front-matter field '{required}'."
            )

    return Skill(
        name=meta["name"],
        description=meta["description"],
        prompt_fragment=parts[2].strip(),
        tags=list(meta.get("tags", [])),
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class Tool(ABC):
    """Base class for all tools exposed to agents.

    Subclasses must set ``name``, ``description``, and ``input_schema``, and
    implement ``run()``. Every backend uses these four attributes — no
    backend-specific conventions required.

    This is the model-callable tool interface. AgenticHarvest domain tools (from
    ``agenticharvest.core.tools``) adapt onto it, so an agent can drive any registered
    domain through the same loop.

    ``input_schema`` is a JSON Schema object describing the tool's parameters,
    e.g.::

        {
            "type": "object",
            "properties": {
                "sheet": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["sheet", "range"],
        }

    Agents call tools via ``__call__``, which delegates to ``run()``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique snake_case identifier, e.g. ``read_range``."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-sentence description shown to the model in the tool declaration."""

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema object describing the tool's input parameters."""

    @abstractmethod
    def run(self, arguments: dict) -> str:
        """Execute the tool and return a string result.

        Args:
            arguments: Key-value arguments matching ``input_schema``.

        Returns:
            Tool output as a plain string. On error, return an error message
            string rather than raising so the model can self-correct.
        """

    def __call__(self, arguments: dict) -> str:
        """Delegates to ``run()`` so backends can call the tool uniformly."""
        return self.run(arguments)

    def schema(self) -> dict:
        """Return an Anthropic-style tool declaration dict.

        Used when building tool schemas to attach to a model request.

        Returns:
            A dict with ``name``, ``description``, and ``input_schema`` keys.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# ---------------------------------------------------------------------------
# AgentConfig and AgentResult
# ---------------------------------------------------------------------------

class AgentConfig(BaseModel):
    """Configuration for an agent instance.

    Attributes:
        model: Model identifier, e.g. ``claude-sonnet-4-6`` or ``gemini-2.0-flash``.
        system_prompt: Base instruction injected before every run.
            Active skills are appended to this at run time.
        skills: Reusable behaviors injected into the system prompt.
            Loaded from Markdown files via ``load_skill()``.
        tags: Free-form labels describing this agent's role/context. A skill
            activates when its ``tags`` are empty or overlap these.
        temperature: Sampling temperature. Defaults to ``0.0`` for determinism.
        max_tokens: Maximum output tokens per turn.
        timeout_seconds: Wall-clock limit for a single run.
        max_retries: Retry budget for the agent's tool-use loop.
    """

    model: str
    system_prompt: str = ''
    skills: list[Skill] = []
    tags: list[str] = []
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES


class AgentResult(BaseModel):
    """The result of a single agent run.

    Attributes:
        content: Final text output produced by the agent.
        messages: Full conversation history for this run, including tool turns.
        tool_calls: All tool invocations made during the run.
        input_tokens: Tokens consumed from the prompt and context.
        output_tokens: Tokens generated by the agent.
        stop_reason: Why the run ended.
    """

    content: str
    messages: list[Message] = []
    tool_calls: list[ToolCall] = []
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: StopReason = StopReason.END_TURN


# ---------------------------------------------------------------------------
# AbstractAgent
# ---------------------------------------------------------------------------

class AbstractAgent(ABC):
    """Base class for all agent backends.

    Every backend subclasses ``AbstractAgent`` and implements ``run()``,
    ``stream()``, ``reflect()``, and ``close()``. The runner composes instances
    according to a chosen pattern — the agent has no pattern awareness.

    Args:
        config: Configuration for this agent instance.
        tools: Tools available during a run.
    """

    def __init__(self, config: AgentConfig, tools: list[Tool]) -> None:
        self.config = config
        self.tools = tools

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short lowercase identifier for the backend, e.g. ``claude``."""

    @abstractmethod
    async def run(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AgentResult:
        """Execute the agent for a single turn and return the result.

        The backend injects the assembled system prompt (base + active skills)
        and any provided context before sending the prompt to the model.

        Context ownership stays with the caller — ``run()`` does not mutate
        or store context between calls.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages to include.

        Returns:
            An ``AgentResult`` with the response, token usage, and tool calls.
        """

    @abstractmethod
    def stream(
        self,
        prompt: str,
        context: list[Message] | None = None,
    ) -> AsyncIterator[str]:
        """Stream the agent response as text chunks.

        Use when low-latency output is needed. Declared as a plain ``def``
        returning an ``AsyncIterator`` because implementations are async
        generators (``async def`` + ``yield``): calling one returns the iterator
        directly, to be consumed with ``async for``, not awaited.

        Args:
            prompt: Instruction for this turn.
            context: Prior conversation messages to include.

        Yields:
            Text chunks as they are produced by the backend.
        """

    @abstractmethod
    async def reflect(self, previous: AgentResult) -> AgentResult:
        """Critique and improve a previous result.

        Called by the runner under the reflection pattern. The agent receives
        its own previous output and produces an improved response.
        Implementations append the previous content as context alongside a
        self-critique prompt.

        Args:
            previous: The ``AgentResult`` to reflect on.

        Returns:
            A new ``AgentResult`` containing the improved response.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release all resources held by the agent.

        Called after all runs are complete. Implementations must close
        sessions, terminate subprocesses, and free connections.
        """

    # ------------------------------------------------------------------
    # Helpers available to all backends
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt by appending active skill fragments.

        A skill is active if its ``tags`` list is empty (applies to all agents)
        or overlaps this agent's ``config.tags``.

        Returns:
            The base ``system_prompt`` with relevant skill fragments appended,
            separated by blank lines.
        """
        parts = [self.config.system_prompt] if self.config.system_prompt else []
        agent_tags = set(self.config.tags)
        for skill in self.config.skills:
            if not skill.tags or agent_tags.intersection(skill.tags):
                parts.append(skill.prompt_fragment)
        return "\n\n".join(parts)
