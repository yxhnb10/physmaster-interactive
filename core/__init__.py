"""Core exports load on demand; policy/UI helpers do not require the model SDK."""
from importlib import import_module

_EXPORTS = {
    'Clarifier': 'clarifier', 'MCTSNode': 'mcts', 'MCTSTree': 'mcts',
    'TrajectorySummarizer': 'summarizer', 'SupervisorOrchestrator': 'supervisor',
    'Theoretician': 'theoretician', 'run_theo_node': 'theoretician',
    'build_mcts_html': 'visualization', 'generate_vis': 'visualization', 'write_mcts_html': 'visualization',
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module('.' + _EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
