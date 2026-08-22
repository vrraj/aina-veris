"""AgentCard definition for the Veris research agent."""

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from backend.a2a.config import VerisA2AAgent, veris_agent_url


def build_veris_agent_card(agent: VerisA2AAgent) -> AgentCard:
    """Build the public contract for one domain-scoped Veris research agent."""
    description = agent.description or (
        f"Answers free-text research prompts using AINA Veris's {agent.domain} knowledge domain."
    )
    return AgentCard(
        name=agent.name,
        description=description,
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=veris_agent_url(agent),
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        default_input_modes=list(agent.input_modes),
        default_output_modes=["text"],
        skills=[
            AgentSkill(
                id=capability_id,
                name=capability_id.replace("_", " ").title(),
                description=(
                    f"Synthesizes sourced answers from Veris's indexed {agent.domain} content. "
                    "Send question, product_ids, and datasheet_ids in an application/json data part, "
                    "or send the question as a text part."
                ),
                tags=["research", "rag", "knowledge", agent.domain, capability_id],
                examples=[f"Summarize the indexed {agent.domain} documents."],
            )
            for capability_id in agent.capability_ids
        ],
    )
