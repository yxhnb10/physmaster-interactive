"""Local account recovery; stop the server before modifying the account files."""
import argparse
import getpass
from pathlib import Path
from urllib.parse import urlparse, urlencode
from utils.team_workspace import TeamStore


def main():
    parser=argparse.ArgumentParser(description='PhysMaster 小组账号管理（操作前停止服务器）')
    parser.add_argument('action',choices=['add-user','reset-password','list','invite','reset-link'])
    parser.add_argument('username',nargs='?')
    parser.add_argument('--admin',action='store_true')
    parser.add_argument('--hours',type=int,default=None,help='链接有效期，1～168 小时')
    parser.add_argument('--public-url',default='https://physmaster.tech',help='浏览器访问的工作台根地址')
    args=parser.parse_args()
    if args.admin and args.action!='add-user':parser.error('--admin 仅适用于 add-user')
    store=TeamStore(Path(__file__).resolve().parent/'team_data')
    if args.action=='list':
        for name,user in store.users.items():print(name,'管理员' if user['admin'] else '成员','已停用' if user.get('disabled') else '可登录')
        return
    if args.action!='invite' and not args.username:parser.error('需要用户名')
    try:
        if args.action in ('invite','reset-link'):
            p=urlparse(args.public_url)
            if p.scheme not in ('https','http') or not p.hostname or p.path not in ('','/') or p.query or p.fragment or p.username or p.password:
                parser.error('--public-url 须为工作台根地址')
            if p.scheme=='http' and p.hostname not in ('localhost','127.0.0.1'):parser.error('公开地址须使用 HTTPS')
            kind='register' if args.action=='invite' else 'reset'
            hours=args.hours if args.hours is not None else (24 if kind=='register' else 1)
            value=store.issue_token(kind,args.username,hours)
            fragment={'invite' if kind=='register' else 'reset':value['token']}
            if kind=='register' and args.username:fragment['username']=args.username
            print('一次性链接（请仅交给对应组员）：')
            print(args.public_url.rstrip('/')+('/register' if kind=='register' else '/reset-password')+'#'+urlencode(fragment))
            print('邀请码／重置码：'+value['token'])
        else:
            password=getpass.getpass('输入密码（至少10位，不回显）：')
            if password!=getpass.getpass('再次输入密码：'):parser.error('两次密码不一致')
            if args.action=='add-user':store.add_user(args.username,password,args.admin)
            else:store.reset_password(args.username,password)
    except ValueError as exc:parser.error(str(exc))
    print('账号配置已保存。请重新启动服务器。')

if __name__=='__main__':main()
