#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview_plan.py — AppShip v0.4.1 / Step 6: Preview 方案

按部署决策生成资源限额 / TTL / 休眠策略 / 预览 URL 规划。

域名策略:
  Preview 域名来自平台配置，不在 Skill 内写死。
  预览环境:       {job_id}.test.appship.top
  正式 Preview:   {job_id}.appship.top
  协议: url_scheme 配置项控制（当前 HTTP，后续可切 HTTPS）。

节点策略: 香港/新加坡（免备案）。正式国内上线切客户域名 + 国内云 + ICP 备案。

配置优先级: config/preview-policy.json > 内置默认值。

用法:
    python scripts/preview_plan.py /path/to/project [--json]
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from deployment_decision import decide

CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'preview-policy.json'

DEFAULTS = {
    # Preview 域名来自平台配置（不写死）:
    #   预览环境 {job_id}.test.appship.top；正式 Preview {job_id}.appship.top
    'preview_domain': 'test.appship.top',
    'preview_domain_prod': 'appship.top',
    'url_scheme': 'http',
    'region_hint': '香港/新加坡（免备案，正式国内上线再切换）',
    'static': {'ttl_hours': 24, 'resource': '静态目录 + Caddy，无常驻进程'},
    'container': {
        'ttl_hours': 24, 'cpu': '0.25-0.4 vCPU', 'memory': '384-512 MB',
        'disk': '1-3GB', 'idle_sleep_minutes': 30, 'wake': '再次访问自动唤醒',
    },
    'compose': {'ttl_hours': 24, 'cpu': '0.5-1 vCPU', 'memory': '512MB-1GB', 'disk': '2GB',
                'idle_sleep_minutes': 30, 'note': '隔离 Compose 环境，避免共享节点干扰'},
    'limits_common': {
        'custom_domain': '免费 Preview 不提供',
        'sla': '免费 Preview 无 SLA',
        'noindex': '所有 Preview 页面 noindex，不进搜索引擎',
        'data': 'Preview 数据与生产完全隔离，禁止存放真实客户数据/生产凭证',
    },
}


def load_policy() -> dict:
    if CONFIG_PATH.is_file():
        try:
            user_cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            policy = json.loads(json.dumps(DEFAULTS))
            for key in ('preview_domain', 'preview_domain_prod', 'url_scheme', 'region_hint'):
                if key in user_cfg:
                    policy[key] = user_cfg[key]
            for group in ('static', 'container', 'compose', 'limits_common'):
                if group in user_cfg and isinstance(user_cfg[group], dict):
                    policy[group].update(user_cfg[group])
            return policy
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULTS


def plan(root: Path) -> dict:
    decision = decide(root)
    dtype = decision['type']
    policy = load_policy()
    job_id = secrets.token_hex(6)
    v = decision['verification']

    # 交付物预览：URL 类才带示例地址；log/report 类无公网地址
    deliverables = []
    for kind in v['deliverable_kinds']:
        item = {'kind': kind}
        if kind in ('web_url', 'api_url'):
            item['url_example'] = f'{job_id}.{policy["preview_domain"]}'
        deliverables.append(item)

    common = {
        'job_id': job_id,
        'preview_url': f'{policy["url_scheme"]}://{job_id}.{policy["preview_domain"]}',
        'preview_domain': policy['preview_domain'],
        'preview_domain_prod': policy['preview_domain_prod'],
        'url_scheme': policy['url_scheme'],
        'region_hint': policy['region_hint'],
        'verification': {
            'execution_mode': v['execution_mode'],
            'transport': v['transport'],
            'probe': v['probe'],
            'deliverables': deliverables,
            'preview_ready': v['preview_ready'],
            'note': v['note'],
        },
    }

    if dtype in ('STATIC', 'STATIC_PLUS_FUNCTION'):
        spec = {
            'mode': 'static',
            'target': decision['preview_target'],
            'resource': policy['static']['resource'],
            'ttl_hours': policy['static']['ttl_hours'],
            'idle_sleep': '不适用（无进程）',
        }
    elif dtype == 'SERVERLESS':
        spec = {
            'mode': 'serverless',
            'target': decision['preview_target'],
            'resource': '函数计算按调用计费，无常驻资源',
            'ttl_hours': policy['static']['ttl_hours'],
            'idle_sleep': '不适用（无常驻实例）',
        }
    elif dtype == 'CONTAINER':
        c = policy['container']
        spec = {
            'mode': 'container',
            'target': decision['preview_target'],
            'cpu': c['cpu'], 'memory': c['memory'], 'disk': c['disk'],
            'ttl_hours': c['ttl_hours'],
            'idle_sleep_minutes': c['idle_sleep_minutes'],
            'wake': c['wake'],
        }
    elif dtype == 'COMPOSE_OR_DEDICATED':
        c = policy['compose']
        spec = {
            'mode': 'compose',
            'target': decision['preview_target'],
            'cpu': c['cpu'], 'memory': c['memory'], 'disk': c['disk'],
            'ttl_hours': c['ttl_hours'],
            'idle_sleep_minutes': c['idle_sleep_minutes'],
            'note': c['note'],
        }
    else:
        spec = {'mode': 'manual', 'target': '人工评估后决定', 'ttl_hours': '-'}

    spec.update(common)
    spec['limits'] = policy['limits_common']
    return {'project': decision['project'], 'deployment_type': dtype, 'preview': spec}


def main():
    parser = argparse.ArgumentParser(description='AppShip Step 6: Preview 方案')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = plan(root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        p = result['preview']
        v = p['verification']
        print(f"项目: {result['project']}  部署类型: {result['deployment_type']}")
        print(f"验证形态: {v['execution_mode']} / {v['transport']} / {v['probe']}")
        for d in v['deliverables']:
            extra = f"  示例: {d['url_example']}" if 'url_example' in d else ''
            print(f"  交付物: {d['kind']}{extra}")
        print(f"  {v['note']}")
        if any(d['kind'] == 'web_url' for d in v['deliverables']):
            print(f"预览地址(示例): {p['preview_url']}")
        print(f"节点建议: {p['region_hint']}")
        print(f"运行方式: {p.get('target', '')}")
        if 'cpu' in p:
            print(f"资源限额: CPU {p['cpu']} / 内存 {p['memory']} / 磁盘 {p['disk']}")
        print(f"TTL: {p['ttl_hours']} 小时后自动销毁")
        if 'idle_sleep_minutes' in p:
            print(f"空闲休眠: {p['idle_sleep_minutes']} 分钟无访问自动停止（{p.get('wake', '支持唤醒')}）")
        print(f"限制: {p['limits']['custom_domain']}；{p['limits']['sla']}")
    return result


if __name__ == '__main__':
    main()
