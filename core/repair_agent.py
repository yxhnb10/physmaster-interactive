"""Bounded repair planning; the runner performs an actual new worker execution."""
import copy
import json
from utils.failure_classifier import REPAIRABLE


def repair_settings(config):
    raw = config.get('repair') or {}
    if not isinstance(raw, dict):
        raise ValueError('repair 必须为对象')
    values = dict(enabled=raw.get('enabled', True), max_node_retries=raw.get('max_node_retries', 1),
                  max_critic_retries=raw.get('max_critic_retries', 1))
    if not isinstance(values['enabled'], bool):
        raise ValueError('repair.enabled 必须为布尔值')
    for key in ('max_node_retries', 'max_critic_retries'):
        if isinstance(values[key], bool) or not isinstance(values[key], int) or not 0 <= values[key] <= 3:
            raise ValueError('repair.' + key + ' 必须是 0～3 的整数')
    return values


class RepairAgent:
    def __init__(self, settings):
        self.settings = settings

    def allowed(self, failure, attempt):
        return self.settings['enabled'] and failure['kind'] in REPAIRABLE and attempt < self.settings['max_node_retries']

    def execute(self, node, payload, failure, attempt, runner):
        """New immutable node owns its artifacts. No relabeling of old files."""
        repaired = copy.deepcopy(payload)
        repaired['node_type'] = 'revise'
        evidence = dict(attempt=attempt, failed_node_id=node.node_id, classification=failure,
                        evaluation=node.evaluation, previous_result=node.result,
                        previous_node_directory=str(payload['task_dir']) + '/node_' + str(node.node_id))
        instruction = ('\n\n## REPAIR ATTEMPT\n' + json.dumps(evidence, ensure_ascii=False, default=str) +
            '\nFix the diagnosed failure, not just its description. Inspect the previous attempt as UNACCEPTED evidence; '
            'write corrected outputs in YOUR NEW node directory and execute the required checks there. '
            'Return non-empty structured JSON, relative file paths, primary_parameters and parameter_evidence. '
            'Do not weaken constraints, alter locked parameters, fabricate receipts or claim files from another node. '
            'A new Critic and program audit will check this attempt. Keep the original subtask scope.')
        repaired['subtask']['description'] += instruction
        repaired['parent_critic_feedback'] = evidence
        return runner(node, repaired, attempt)
