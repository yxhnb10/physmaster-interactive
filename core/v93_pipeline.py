"""The live node controller used by Supervisor, including report-only nodes.

An initial solver batch may be parallel. Every returned attempt follows this
same review/audit/classify/repair path; a repair is a real new worker/node.
Publication is deferred to selected-branch completion (ArtifactManager).
"""
import time
from pathlib import Path
from core.repair_agent import RepairAgent
from core.research_lifecycle import stage_for
from utils.artifact_manager import ArtifactManager
from utils.critic_policy import gate_evaluation
from utils.failure_classifier import classify_failure
from utils.node_scores import score_node
from utils.research_integrity import normalize_node_paths


class NodePipeline:
    def __init__(self, supervisor):
        self.owner = supervisor
        self.repair = RepairAgent(supervisor.repair_settings)
        self.artifacts = ArtifactManager(supervisor.task_dir)

    def _review(self, node):
        owner = self.owner
        history = []
        limit = owner.repair_settings['max_critic_retries'] if owner.repair_settings['enabled'] else 0
        for attempt in range(limit + 1):
            started = time.time()
            try:
                evaluation = owner._call_critic(node) or {}
            except Exception as exc:
                evaluation = gate_evaluation({}, owner.critic_settings)
                evaluation['critic_error'] = type(exc).__name__ + ': ' + str(exc)
            valid = evaluation.get('score_valid') is True and evaluation.get('review_valid') is not False and not evaluation.get('critic_error')
            history.append(dict(attempt=attempt + 1, started_at=started, finished_at=time.time(),
                                valid=valid, error=evaluation.get('critic_error'), evaluation=dict(evaluation)))
            node.review_history = history
            if valid or attempt == limit:
                break
            node.evaluation = evaluation
            owner.monitor.node(node, 'reevaluating')
        evaluation['review_attempts'] = history
        return evaluation

    def process(self, node, output, payload, subtask):
        owner = self.owner
        nodes = []
        attempt = 0
        while True:
            node.research_stage = stage_for(subtask)
            node.repair_attempt = attempt
            node.pipeline_version = 'v9.3-complete'
            node.theoretician_output = output.get('result')
            node.result, corrections = normalize_node_paths(owner.structured_problem, node.theoretician_output,
                                                           Path(owner.task_dir) / f'node_{node.node_id}')
            node.log_path = output.get('log_path')
            tools = output.get('tool_calls', [])
            self.artifacts.record_node(node.node_id, node.result, {})
            owner.monitor.node(node, 'evaluating', tools)
            evaluation = self._review(node)
            evaluation = owner._audit_node_result(node, subtask, tools, evaluation)
            failure = classify_failure(node.result, evaluation, tools)
            evaluation.update(failure_class=failure['kind'], failure_reasons=failure['reasons'],
                              pipeline_action=failure['action'], research_stage=node.research_stage,
                              pipeline_version='v9.3-complete')
            evaluation = score_node(node.result, evaluation, subtask, tools)
            evaluation['final_decision'] = evaluation['decision']
            evaluation['critic_decision'] = evaluation.get('model_decision')
            evaluation['decision_mismatch'] = evaluation.get('model_decision') != evaluation['decision']
            evaluation['decision_reasons'] = list(dict.fromkeys(filter(None, [evaluation.get('policy_reason')] +
                evaluation.get('integrity_audit', {}).get('issues', []) +
                evaluation.get('integrity_audit', {}).get('unchecked', []))))
            node.evaluation = evaluation
            node.reward = owner._extract_reward(evaluation)
            manifest = self.artifacts.record_node(node.node_id, node.result, evaluation)
            node.artifact_manifest = manifest
            will_repair = self.repair.allowed(failure, attempt)
            node.repair_status = ('queued' if will_repair else 'succeeded' if attempt and failure['kind'] == 'NONE' else
                                  'exhausted' if failure['action'] == 'repair_then_retry' else
                                  'review_exhausted' if failure['kind'] == 'CRITIC_FAILURE' else 'not_needed')
            owner._finish_pipeline_node(node, output)
            nodes.append(node)
            if not will_repair:
                break
            node.repair_status = 'running'
            owner.monitor.node(node, 'repairing', tools)
            previous = node
            node, output = self.repair.execute(previous, payload, failure, attempt + 1, owner._execute_repair)
            previous.repair_status = 'retried'
            previous.repair_child_id = node.node_id
            owner.monitor.node(previous, 'finished', tools)
            attempt += 1
        # Show chain outcome without upgrading any failed parent's acceptance.
        for previous in nodes[:-1]:
            previous.repair_outcome = dict(node_id=node.node_id, accepted=node.evaluation.get('decision') == 'complete',
                                           failure_class=node.evaluation.get('failure_class'))
            owner.monitor.node(previous, 'finished')
        return nodes
