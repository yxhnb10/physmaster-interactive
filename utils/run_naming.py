"""Human-readable run names; task IDs and research requirements stay separate."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

RESEARCH_TIMEZONE = timezone(timedelta(hours=8), 'Asia/Shanghai')
MAX_TOPIC_LENGTH = 24
TASK_ID = re.compile(r'[a-f0-9]{32}')


def clean_topic(text, fallback='未命名研究'):
    text = unicodedata.normalize('NFKC', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.category(c).startswith('C'))
    text = re.sub(r'[<>:"/\\|?*]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' ._-#')
    text = text[:MAX_TOPIC_LENGTH].rstrip(' ._-')
    return text or fallback


def research_topic(query, explicit=None):
    if explicit is not None and (not isinstance(explicit, str) or len(explicit) > 120):
        raise ValueError('研究名称必须是文本，最多120字符')
    if explicit and explicit.strip():
        return clean_topic(explicit)
    for line in str(query).splitlines():
        line = re.sub(r'^\s*#{1,6}\s*', '', line).strip()
        if not line or line.casefold().rstrip(':：') in ('task', '任务', '问题', '研究任务', 'query', 'instruction'):
            continue
        line = re.sub(r'^(?:Problem|Question)\s+[A-Za-z0-9]+\s*[:：]\s*', '', line, flags=re.I)
        return clean_topic(line)
    return '未命名研究'


def valid_round(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 999999 else None


def run_label(created_at, topic, round_number):
    date = datetime.fromtimestamp(created_at, RESEARCH_TIMEZONE)
    return f'{date:%Y-%m-%d}_{clean_topic(topic)}_第{round_number:02d}轮研究_{date:%H%M%S}'


def naming_fields(job):
    folder = job['task_dir'].parent.name
    label = run_label(job['created_at'], job['research_topic'], job['research_round'])
    display_name = label if TASK_ID.fullmatch(folder) else folder
    return dict(research_topic=job['research_topic'], research_round=job['research_round'],
        research_id=job['research_id'], display_name=display_name,
        summary_filename_base=display_name, naming_timezone='Asia/Shanghai (UTC+08:00)')
