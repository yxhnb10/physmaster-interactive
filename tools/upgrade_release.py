"""Apply an explicit release file manifest, with backup and rollback. Stdlib only."""
import argparse
import hashlib
import json
import os
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained(root, relative):
    rel=PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(x in ('..','.') for x in rel.parts) or '\\' in relative or ':' in relative:
        raise ValueError('Invalid release path: '+relative)
    if rel.parts[0] in ('outputs','config.yaml','.env','.git','.venv','venv'):
        raise ValueError('Protected user data path: '+relative)
    path=root.joinpath(*rel.parts)
    current=path
    while current!=root:
        if current.is_symlink():raise ValueError('Refusing symlink: '+str(current))
        current=current.parent
    return path


def replace(source, target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.upgrade')
    try:
        shutil.copyfile(source,temporary)
        for attempt in range(30):
            try:
                os.replace(temporary,target);return
            except PermissionError:
                if attempt==29:raise
                time.sleep(.1)
    finally:
        temporary.unlink(missing_ok=True)


def apply(bundle, project):
    bundle=Path(bundle).resolve();project=Path(project).resolve()
    if not (project/'run.py').is_file() or not (project/'web_server.py').is_file():
        raise ValueError('目标必须是包含 run.py 和 web_server.py 的项目目录')
    manifest=json.loads((bundle/'upgrade-manifest.json').read_text(encoding='utf-8'))
    records=manifest['files'];removed=manifest.get('removed',[])
    if manifest.get('version') == 'v9.4-incremental' and not (project/'core/v93_pipeline.py').is_file():
        raise ValueError('此补丁要求 v9.3 Complete 基础版本；未找到 core/v93_pipeline.py')
    names=[r['path'] for r in records]+removed
    if len(names)!=len(set(names)):raise ValueError('Duplicate release paths')
    for row in records:
        source=contained(bundle/'payload',row['path'])
        target=contained(project,row['path'])
        if not source.is_file() or digest(source)!=row['sha256']:
            raise ValueError('升级包校验失败：'+row['path'])
        if target.exists() and not target.is_file():raise ValueError('目标不是普通文件：'+row['path'])
    for name in removed:contained(project,name)
    backup_dir=project/'upgrade_backups'
    if backup_dir.is_symlink():raise ValueError('备份目录不能为符号链接')
    backup_dir.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    backup=backup_dir/('before-v94-'+stamp+'.zip')
    existed=[name for name in names if contained(project,name).is_file()]
    new=[r['path'] for r in records if r['path'] not in existed]
    with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
        for name in existed:z.write(contained(project,name),name)
        z.writestr('RESTORE.json',json.dumps(dict(release=manifest.get('version'),new_files=new,restore_files=existed),ensure_ascii=False,indent=2))
    changed=[]
    try:
        for row in records:
            target=contained(project,row['path'])
            replace(contained(bundle/'payload',row['path']),target)
            changed.append(row['path'])
            if digest(target)!=row['sha256']:raise ValueError('升级后校验失败：'+row['path'])
        for name in removed:
            target=contained(project,name)
            if target.exists():target.unlink();changed.append(name)
    except Exception:
        with zipfile.ZipFile(backup) as z:
            recovery=backup_dir/('recovery-'+stamp);recovery.mkdir()
            try:
                for name in changed:
                    target=contained(project,name)
                    if name in existed:
                        source=contained(recovery,name);source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(z.read(name));replace(source,target)
                    elif target.exists():target.unlink()
            finally:shutil.rmtree(recovery)
        raise
    return dict(version=manifest.get('version'),updated=len(records),removed=sum(n in existed for n in removed),backup=str(backup))


def main():
    parser=argparse.ArgumentParser(description='PhysMaster v9.4 Incremental upgrade; stop the server first.')
    parser.add_argument('--project',required=True,help='Existing PhysMaster project directory')
    parser.add_argument('--bundle',default=str(Path(__file__).resolve().parent),help='Extracted upgrade bundle directory')
    args=parser.parse_args()
    result=apply(args.bundle,args.project)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('升级完成；config.yaml 与 outputs 未修改。请重新启动服务器并刷新网页。')


if __name__=='__main__':main()
