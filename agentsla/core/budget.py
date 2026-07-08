"""Budget manager for tokens, cost, and wall-clock latency per request.

Plan 2 implementation; ``mypy --strict``-compatible interface lives here so
package consumers can ``from agentsla.core.budget import BudgetManager`` even
before the body lands.
"""
