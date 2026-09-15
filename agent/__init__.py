"""
Waffarha Agentic Intelligence Layer.

The agent orchestrates the existing deterministic capabilities (faceted
catalog, hybrid retrieval, catalog/personal services, comparison logic)
behind a registry of safe, typed tools. It is NOT a better intent router:
the model reasons about what the user wants, which entities/constraints are
involved, which tools to use, and whether the returned evidence satisfies
the request. Deterministic business logic stays authoritative; the agent
provides controlled decision-making and orchestration on top of it.

Pipeline (bounded, explicit state machine -- no LangGraph dependency):

    understanding -> plan -> tool execution -> observe/validate
                                        |-> replan/relax (deterministic)
                                        |-> clarification
                                        -> grounded response

No private chain-of-thought is ever exposed; every step emits a structured
decision trace (goal, constraints, tool selection, evidence summary, next
action) that is safe to show to users and logs.
"""