"""The old graph-shaped entry points (``build_graph().invoke(state)``, ``decide_node(state)``),
rebuilt on the new code so the scenario tests written against them keep running unchanged.

Test support only. Not a ``conftest.py`` on purpose: pytest registers every conftest as the
top-level module ``conftest``, and a core test imports ``bar`` from the core one by that name.
"""

from fin_core.scoring import run_state
from fin_domain.scoring import Assessment
from fin_domain.signals import decide_signal


class _Graph:
    def invoke(self, state):
        return run_state(
            state["metrics"],
            state["config"],
            state.get("previous_conviction"),
            buy_armed=state.get("buy_armed", True),
            sell_armed=state.get("sell_armed", True),
        )


def build_graph():
    return _Graph()


def decide_node(state):
    explanation = state.get("explanation", "")
    assessment = Assessment(
        metrics=state["metrics"],
        conviction=state.get("conviction"),
        all_abstained=state.get("all_abstained", False),
        explanation=explanation,
        philosophy_results={},
    )
    decision = decide_signal(
        assessment,
        previous_conviction=state.get("previous_conviction"),
        buy_armed=state.get("buy_armed", True),
        sell_armed=state.get("sell_armed", True),
    )
    out = {"signal": decision.signal}
    if decision.explanation != explanation:
        out["explanation"] = decision.explanation
    return out
