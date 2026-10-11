"""P13 real HTTP/MySQL verification, including concurrent atomic deferrals.

Reuse FastAPI: never starts a duplicate server; never uses MCP or a model.
All temporary records are tracked and deleted in finally, demos remain untouched.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import secrets
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tests.manual.e2e_mcp import API, START_API, CheckFailed, check, headers, login, request, success
import httpx


def run(output=None):
    records = []
    created = []
    course_id = user_id = None
    with httpx.Client(base_url=API,timeout=30) as client:
        try:
            try:
                client.get('/').raise_for_status()
            except httpx.RequestError:
                raise CheckFailed('FastAPI 未运行，请启动：\n'+START_API) from None
            tokens = {name:login(client,name,'admin' if name=='admin' else 'student')[0]
                      for name in ('aaa','bbb','admin')}
            def call(method,path,owner='aaa',payload=None,code=200):
                kwargs = {'headers':headers(tokens[owner])} if owner else {}
                if payload is not None:
                    kwargs['json'] = payload
                status,body = request(client,method,path,**kwargs)
                check(body.get('code')==code, f'{method} {path}: 预期 {code} 实际 {body.get("code")} {body.get("message")}')
                records.append({'method':method,'path':path,'owner':owner,'http_status':status,'response':body})
                return body.get('data')

            def create(resource,owner='aaa',**payload):
                row = call('POST','/api/'+resource,owner,payload)
                created.append((resource,row['id'],owner))
                return row

            marker = 'P13-'+uuid4().hex[:12]
            course = call('POST','/api/course','admin',{'name':marker+'验证课程','code':marker})
            course_id = course['id']
            temporary_password = secrets.token_urlsafe(24)
            user = call('POST','/api/user','admin',{'username':marker,'name':'P13临时验证学生',
                        'password':temporary_password,'role':'student'})
            user_id = user['id']
            # Use the real login contract, including the current password version.
            temporary_login = success(client, 'POST', '/api/auth/login',
                                      json={'username': marker, 'password': temporary_password})
            tokens['temporary'] = temporary_login['token']
            base = {'course_id':course_id,'type':'assignment','title':'第3章课后习题',
                    'due_at':'2026-10-12T18:00:00'}
            assignment = create('assignments',**base)
            call('PUT',f'/api/assignments/{assignment["id"]}',payload={'status':'done'})
            call('GET',f'/api/assignments?course_id={course_id}&type=assignment&status=done&page=1&page_size=1')
            for owner in ('bbb','admin'):
                call('PUT',f'/api/assignments/{assignment["id"]}',owner,{'title':'越权修改'},404)
                call('DELETE',f'/api/assignments/{assignment["id"]}',owner,code=404)
                data = call('GET',f'/api/assignments?course_id={course_id}&user_id={assignment["user_id"]}',owner)
                check(data['total']==0,'用户筛选发生越权')
            for field,value in (('type','bad'),('status','bad'),('due_at','2026-13-99T18:00:00')):
                call('PUT',f'/api/assignments/{assignment["id"]}',payload={field:value},code=400)
            call('PUT',f'/api/assignments/{assignment["id"]}',payload={'course_id':2147483647},code=404)
            call('DELETE',f'/api/course/{course_id}','admin',code=409)
            task = create('plan-tasks',course_id=course_id,title='复习栈与队列',due_at='2026-10-12T20:00:00')
            for owner in ('bbb','admin'):
                call('POST',f'/api/plan-tasks/{task["id"]}/defer',owner,{'days':1},404)
                call('PUT',f'/api/plan-tasks/{task["id"]}',owner,{'title':'越权修改'},404)
                call('DELETE',f'/api/plan-tasks/{task["id"]}',owner,code=404)
            task = call('POST',f'/api/plan-tasks/{task["id"]}/defer',payload={})
            before = datetime.fromisoformat(task['due_at'])
            def defer(_):
                status,body = request(client,'POST',f'/api/plan-tasks/{task["id"]}/defer',
                    headers=headers(tokens['aaa']),json={'days':1})
                check(status==200 and body.get('code')==200,'并发延期失败')
                return body['data']['defer_count']
            with ThreadPoolExecutor(max_workers=8) as pool:
                counters = list(pool.map(defer,range(8)))
            listed = call('GET','/api/plan-tasks?page_size=100')
            actual = next(row for row in listed['list'] if row['id']==task['id'])
            check(actual['defer_count']==9,'并发延期计数丢失')
            check(datetime.fromisoformat(actual['due_at'])==before+timedelta(days=8),'并发延期截止时间丢失')
            print('[PASS] 8 个并发延期：计数 1→9；截止时间再推后8天')
            temp_task = create('plan-tasks',owner='temporary',title='临时学生的计划任务')
            call('DELETE',f'/api/user/{user_id}','admin',code=409)
            call('GET','/api/plan-tasks',owner=None,code=401)
            call('GET','/api/assignments',owner=None,code=401)
            print('[PASS] CRUD / 分页筛选 / 越权404 / 非法400 / 课程与用户关联409 / 未登录401')
        finally:
            cleanup_errors = []
            for resource,item_id,owner in reversed(created):
                status,body = request(client,'DELETE',f'/api/{resource}/{item_id}',headers=headers(tokens[owner]))
                if body.get('code') not in (200,404):
                    cleanup_errors.append(f'{resource}/{item_id}')
            if not cleanup_errors:
                for path in ([f'/api/user/{user_id}'] if user_id else []) + ([f'/api/course/{course_id}'] if course_id else []):
                    status,body = request(client,'DELETE',path,headers=headers(tokens['admin']))
                    if body.get('code') not in (200,404):
                        cleanup_errors.append(path)
            check(not cleanup_errors,'临时数据清理失败：'+','.join(cleanup_errors))
            if output:
                Path(output).write_text(json.dumps({'records':records,'cleanup':'completed'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print('[PASS] 临时任务、作业、用户与课程均已清理')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        run(args.output)
    except CheckFailed as exc:
        print('FAIL: '+str(exc),file=sys.stderr)
        raise SystemExit(1)
