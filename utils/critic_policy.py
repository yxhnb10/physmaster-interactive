"""Validated Critic settings and conservative score gates."""
import math

DEFAULTS = dict(accept_threshold=0.85, redraft_threshold=0.60,
                retrieval_threshold=0.50, retrieval_enabled=False)


def merge_critic_settings(base, overrides=None):
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError('critic_settings 必须是对象')
    unknown = set(overrides or {}) - set(DEFAULTS)
    if unknown:
        raise ValueError('未知 Critic 设置：' + ', '.join(sorted(unknown)))
    values = dict(base, **(overrides or {}))
    for key in ('accept_threshold', 'redraft_threshold', 'retrieval_threshold'):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Critic 阈值必须是数字：' + key)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Critic 阈值必须在 0～1 之间：' + key)
        values[key] = float(value)
    if values['redraft_threshold'] >= values['accept_threshold']:
        raise ValueError('重做阈值必须小于通过阈值')
    if not isinstance(values['retrieval_enabled'], bool):
        raise ValueError('检索评审开关必须为 true 或 false')
    return values


def critic_settings_from_config(config):
    critic = config.get('critic') or {}
    tools = config.get('tools') or {}
    retrieval = (tools.get('retrieval_critic') or {}) if isinstance(tools, dict) else None
    if not isinstance(critic, dict) or not isinstance(retrieval, dict):
        raise ValueError('Critic 配置节点必须是对象')
    return merge_critic_settings(DEFAULTS, dict(
        accept_threshold=critic.get('accept_threshold', DEFAULTS['accept_threshold']),
        redraft_threshold=critic.get('redraft_threshold', DEFAULTS['redraft_threshold']),
        retrieval_threshold=retrieval.get('threshold', DEFAULTS['retrieval_threshold']),
        retrieval_enabled=retrieval.get('enabled', DEFAULTS['retrieval_enabled'])))


def apply_critic_settings(config, values):
    config.setdefault('critic', {}).update(accept_threshold=values['accept_threshold'],
                                        redraft_threshold=values['redraft_threshold'])
    config.setdefault('tools', {}).setdefault('retrieval_critic', {}).update(
        threshold=values['retrieval_threshold'], enabled=values['retrieval_enabled'])


def normalized_score(value):
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None


def gate_evaluation(parsed, settings):
    model_decision = str(parsed.get('decision', 'to_revise')).strip().lower()
    if model_decision not in ('complete', 'to_revise', 'to_redraft'):
        model_decision = 'to_revise'
    score = normalized_score(parsed.get('reward'))
    decision = model_decision
    reason = ''
    if score is None:
        score = 0.0
        decision = 'to_revise'
        reason = '评分缺失或无效，不能认定完成；需要重新评审。'
    elif score < settings['redraft_threshold']:
        decision = 'to_redraft'
        reason = '评分低于重做阈值。'
    elif score < settings['accept_threshold'] and decision == 'complete':
        decision = 'to_revise'
        reason = '评分未达到通过阈值，不能认定完成。'
    return dict(decision=decision, verdict={'complete':'accept','to_revise':'refine',
        'to_redraft':'reject'}[decision], reward=score, model_decision=model_decision,
        threshold_adjusted=decision != model_decision, policy_reason=reason,
        score_valid=normalized_score(parsed.get('reward')) is not None,
        critic_policy={'accept_threshold':settings['accept_threshold'],
                       'redraft_threshold':settings['redraft_threshold']})
