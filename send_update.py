"""Submit a natural-language update from a second terminal."""
import argparse
from pathlib import Path
from utils.live_updates import submit_update

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--task-dir', required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--file', help='UTF-8 text file')
    args = parser.parse_args()
    text = args.text if args.text is not None else Path(args.file).read_text(encoding='utf-8')
    path = submit_update(args.task_dir, text)
    print(f'Queued: {path.name}')
    print(f'Applied receipt will appear at: {path.parent / "acks" / path.name}')
