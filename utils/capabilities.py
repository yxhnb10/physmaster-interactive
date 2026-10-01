"""Allowlisted task-level capability settings shared by UI and worker."""

CAPABILITIES = (
    dict(id='web_search', label='网页搜索 WebSearch', path=('tools', 'web_enabled'), default=False,
         description='调用已配置的网页搜索服务，如 Tavily；需要有效密钥。'),
    dict(id='arxiv_search', label='arXiv 论文检索', path=('landau', 'library_enabled'), default=True,
         description='独立的联网论文检索；关闭 WebSearch 不会关闭它。'),
    dict(id='codata', label='CODATA 常数查询', path=('tools', 'codata_enabled'), default=True,
         description='从本地常数表查询物理常数。'),
    dict(id='python', label='Python 代码执行', path=('tools', 'python_enabled'), default=True,
         description='允许求解节点执行 Python；关闭后只能给出未执行的代码或分析。'),
    dict(id='skills', label='Skills 技能加载', path=('skills', 'enabled'), default=True,
         description='向求解节点提供技能说明，并允许加载技能文件。'),
    dict(id='workflow', label='Workflow 方法流程', path=('landau', 'workflow_enabled'), default=True,
         description='澄清问题时匹配已有研究流程。'),
    dict(id='prior', label='Prior 先验知识检索', path=('landau', 'prior_enabled'), default=True,
         description='检索已有向量知识库；需要预先准备索引和依赖。'),
)


def capability_values(config):
    result = {}
    for spec in CAPABILITIES:
        value = config
        for key in spec['path']:
            value = value.get(key, spec['default']) if isinstance(value, dict) else spec['default']
        if not isinstance(value, bool):
            raise ValueError('能力配置必须为布尔值：' + spec['id'])
        result[spec['id']] = value
    return result


def merge_capabilities(base, overrides=None):
    if overrides is None:
        return dict(base)
    if not isinstance(overrides, dict):
        raise ValueError('capabilities 必须是对象')
    unknown = set(overrides) - set(base)
    if unknown:
        raise ValueError('未知能力开关：' + ', '.join(sorted(unknown)))
    if any(not isinstance(value, bool) for value in overrides.values()):
        raise ValueError('能力开关只能是 true 或 false')
    return dict(base, **overrides)


def apply_capabilities(config, values):
    for spec in CAPABILITIES:
        section = config
        for key in spec['path'][:-1]:
            section = section.setdefault(key, {})
            if not isinstance(section, dict):
                raise ValueError('能力配置节点必须是对象：' + key)
        section[spec['path'][-1]] = values[spec['id']]


def public_capabilities(config):
    return dict(defaults=capability_values(config), options=[
        {key: spec[key] for key in ('id', 'label', 'description')} for spec in CAPABILITIES])
