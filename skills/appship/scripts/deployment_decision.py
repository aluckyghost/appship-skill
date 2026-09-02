#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deployment_decision.py — AppShip v0.4.1 / Step 4: 部署决策引擎

不默认 Docker 化。按运行形态选择最便宜、最简单的部署方式:
  STATIC                 纯静态 → OSS/COS + CDN
  STATIC_PLUS_FUNCTION   静态前端 + 轻函数 → 静态托管 + Serverless
  SERVERLESS             极轻无状态 API（项目自带 serverless 声明）→ 函数计算
  CONTAINER              单服务动态 → 容器
  COMPOSE_OR_DEDICATED   多服务/Redis/Worker → Compose / 独立服务器
  MANUAL_REVIEW          高风险/特殊依赖 → 人工评估

报告必须回答（v0.5 §6）: 为什么 / 最低资源 / 是否需 Docker /
是否需独立 DB / Redis 是否真必须 / 国内还是海外。

用法:
    python scripts/deployment_decision.py /path/to/project [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from detect_stack import detect

# 各类型最低资源建议
RESOURCES = {
    'STATIC': '无常驻计算资源。OSS/COS 存储费 + CDN 流量费（约 ¥10/月起，小流量近乎免费）',
    'STATIC_PLUS_FUNCTION': '静态托管 + 函数计算按调用计费，无常驻资源（低频调用近乎免费）',
    'SERVERLESS': '函数计算按调用计费。单并发实例约 0.25 vCPU / 256MB，冷启动 0.5-2s',
    'CONTAINER': '最低 0.5 vCPU / 512MB 常驻；建议 1 vCPU / 1GB（2C4G 服务器可跑 2-3 个容器）',
    'COMPOSE_OR_DEDICATED': '独享 2C4G 服务器起步（多服务共享一台，含 DB/Redis/Worker）',
    'MANUAL_REVIEW': '评估后给出',
}

# 各类型国内/海外建议
REGION = {
    'STATIC': '国内外访问均快（CDN 全球加速），按用户所在地选区域',
    'STATIC_PLUS_FUNCTION': '国内用户选国内云函数（需备案）；海外用户选 Vercel/Netlify 类免备案',
    'SERVERLESS': '国内用户选函数计算（域名需备案）；海外选 Vercel/Cloudflare Workers',
    'CONTAINER': '国内用户选国内云（需备案）；出海选香港/新加坡（免备案延迟低）',
    'COMPOSE_OR_DEDICATED': '国内用户选国内 ECS；出海选香港/新加坡轻量服务器',
    'MANUAL_REVIEW': '评估后给出',
}

# ---------- 验证环境分类学（Preview ≠ 域名） ----------
# 验证形态四轴，与部署类型（dtype）正交：
#   execution_mode  service（长期进程）| run_once（执行一次出结果）
#   transport       http | tcp | stdio | none        （如何对外暴露）
#   probe           http | tcp | stdio | exit_code | none（成功判据）
#   deliverable_kinds  交付物类型。Endpoint 只是 Deliverable 的一种，
#                      不能反过来让 Deliverable 成为 Endpoint 的子类：
#                      web_url / api_url / test_page / log / validation_report / artifact
# MVP Runner 实际支持: service + (http|tcp) 探活；run_once / stdio / http 探针属 P6。

WEB_FRAMEWORKS = {'FastAPI', 'Flask', 'Django', 'Tornado', 'Sanic', 'Starlette',
                  'Express', 'Koa', 'Fastify', 'NestJS', 'Next.js'}


def verify_axes(stack: dict, dtype: str) -> dict:
    """由检测结果推导验证形态四轴。"""
    hints = stack['hints']
    rt = stack['runtime']
    fws = set(stack['frameworks'])
    has_web = rt in ('python_service',) or (rt == 'node' and bool(fws & WEB_FRAMEWORKS))

    def axes(mode, transport, probe, kinds, note, ready):
        return {'execution_mode': mode, 'transport': transport, 'probe': probe,
                'deliverable_kinds': kinds, 'preview_ready': ready, 'note': note}

    # 1) MCP Server：长期进程但不监听端口（stdio 上的 MCP 协议）
    #    注意 MCP 不等于 run_once —— 它是 service + stdio transport
    if hints.get('mcp') and not has_web:
        return axes('service', 'stdio', 'stdio', ['log', 'validation_report'],
                    'MCP Server（stdio 传输）：沙箱运行 + MCP initialize 握手验证，无公网 URL', False)

    # 2) CLI / 纯脚本 / 纯 Worker：跑一次看退出码与输出
    is_cli = hints.get('cli') and not has_web
    worker_only = hints.get('workers') and not has_web and rt != 'static_build'
    if is_cli or rt == 'python' or worker_only:
        kind = 'CLI 工具' if is_cli else ('Worker/队列任务' if worker_only else '一次性脚本')
        return axes('run_once', 'none', 'exit_code', ['log', 'validation_report'],
                    f'{kind}：沙箱执行一次，按退出码/输出验证，无公网 URL', False)

    # 3) 静态：http 文件服务，无进程探针
    if dtype in ('STATIC', 'STATIC_PLUS_FUNCTION'):
        return axes('service', 'http', 'none', ['web_url'],
                    '静态托管，交付临时网址', True)

    # 4) Web / API / Agent 服务：临时域名反代（MVP 探针为 TCP，HTTP 探针 P6 升级）
    if dtype in ('SERVERLESS', 'CONTAINER', 'COMPOSE_OR_DEDICATED'):
        api_like = bool(fws & (WEB_FRAMEWORKS - {'Next.js'})) or hints.get('agent')
        kinds = ['api_url'] if api_like else ['web_url']
        note = ('临时 API 地址交付' if api_like else '临时网址交付') + \
               '（MVP 探针 TCP，HTTP /health 探针 P6 升级）'
        return axes('service', 'http', 'tcp', kinds, note, True)

    return axes('service', 'none', 'none', ['validation_report'],
                '无法识别运行形态，验证方式人工评估', False)


def decide(root: Path) -> dict:
    stack = detect(root)
    rt = stack['runtime']
    svc = stack['services']
    hints = stack['hints']
    market = stack['target_market']

    reason = ''
    docker_required = False
    preview_target = ''

    # 高风险特征 → 人工评估
    risky = False
    for f in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml', 'Dockerfile'):
        p = root / f
        if p.is_file():
            t = p.read_text(encoding='utf-8', errors='ignore')
            if 'privileged' in t or 'docker.sock' in t or 'cap_add' in t or '--gpus' in t:
                risky = True

    has_db = bool(svc['database'])
    has_cache = bool(svc['cache'])
    has_queue = bool(svc['queue'])
    has_storage = bool(svc['storage'])
    has_workers = bool(hints['workers'])
    has_agent = bool(hints.get('agent'))

    if risky:
        dtype = 'MANUAL_REVIEW'
        reason = '检测到特权容器/docker.sock/GPU 等高风险配置，需人工评估'
        preview_target = '人工评估后决定'
    elif rt in ('static_html', 'static_build'):
        if hints['serverless'] or has_db:
            dtype = 'STATIC_PLUS_FUNCTION'
            reason = '静态前端 + 函数/数据依赖，适合静态托管 + Serverless/云函数'
            preview_target = '静态托管 + Function'
        else:
            dtype = 'STATIC'
            reason = '纯静态输出，直接 OSS/COS + CDN，无常驻 CPU/RAM 成本'
            preview_target = 'OSS/静态托管 + CDN'
        docker_required = False
    elif hints['serverless'] and rt in ('node', 'python_service') and not has_agent \
            and not has_cache and not has_queue and not has_workers:
        # 项目自带 serverless 声明（vercel.json / netlify.toml / wrangler.toml）且为极轻无状态 API
        dtype = 'SERVERLESS'
        reason = '项目声明了 Serverless 意图且无重依赖（无 DB/Redis/Worker），适合函数计算'
        preview_target = '函数计算 / Serverless'
        docker_required = False
    elif rt in ('node', 'python_service', 'python'):
        multi = (has_cache or has_queue or has_workers
                 or stack['existing_docker']['compose'])
        if multi:
            dtype = 'COMPOSE_OR_DEDICATED'
            reason = '多服务形态（DB/Redis/Worker/Compose），需 Docker Compose 或独立服务器'
            preview_target = '隔离 Compose / 专用 Preview 节点'
            docker_required = True
        else:
            dtype = 'CONTAINER'
            if has_agent:
                reason = 'Agent/LLM 应用（流式长连接），Serverless 不适合，容器部署最稳妥'
            else:
                reason = '单一动态服务，共享 Preview 节点容器部署，资源限额 + TTL'
            preview_target = '共享容器节点'
            docker_required = True
    else:
        dtype = 'MANUAL_REVIEW'
        reason = '无法识别运行形态，需人工确认技术栈'
        preview_target = '人工评估'

    # 是否需要独立 DB
    if dtype in ('STATIC', 'STATIC_PLUS_FUNCTION') and not has_db:
        db_advice = '不需要（纯静态，无数据库）'
        needs_external_db = False
    elif has_db:
        needs_external_db = True
        db_advice = f"需要。检测到数据库依赖（{', '.join(svc['database'][:3])}），生产建议云数据库（RDS/MongoDB 等），不建议容器内自建"
    else:
        needs_external_db = False
        db_advice = '暂无数据库依赖；若后续引入，生产建议云数据库'

    # Redis 是否真的必须
    if has_cache:
        redis_required = True
        redis_advice = '检测到 Redis 依赖。生产环境建议用托管 Redis（按量付费小规格即可）；若仅做缓存可评估降级为本地缓存，但 Session/Queue 场景必须保留'
    else:
        redis_required = False
        redis_advice = '不需要'

    # 国内/海外建议
    if market == 'china':
        region_advice = '依赖检测到国内特有服务（微信/支付宝等）→ 建议国内部署（域名需 ICP 备案）。' + REGION[dtype]
    elif market == 'global':
        region_advice = '依赖检测到海外服务（Stripe/Google 等）→ 建议海外部署（免备案）。' + REGION[dtype]
    else:
        region_advice = '未检测到明确市场倾向。' + REGION[dtype]

    return {
        'project': stack['project'],
        'stack': stack,
        'type': dtype,
        'reason': reason,
        'preview_target': preview_target,
        'docker_required': docker_required,
        # 验证环境四轴（Preview ≠ 域名；Endpoint 只是 Deliverable 的一种）
        'verification': verify_axes(stack, dtype),
        # v0.5 §6 报告必答四项
        'resources': RESOURCES[dtype],
        'needs_external_db': needs_external_db,
        'db_advice': db_advice,
        'redis_required': redis_required,
        'redis_advice': redis_advice,
        'region_advice': region_advice,
    }


def main():
    parser = argparse.ArgumentParser(description='AppShip Step 4: 部署决策引擎')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = decide(root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        v = result['verification']
        print(f"项目: {result['project']}")
        print(f"部署类型: {result['type']}")
        print(f"Docker 需要: {'是' if result['docker_required'] else '否'}")
        print(f"验证形态: {v['execution_mode']} / {v['transport']} / {v['probe']}"
              f"  交付: {', '.join(v['deliverable_kinds'])}")
        print(f"  {v['note']}")
        print(f"Preview 方式: {result['preview_target']}")
        print(f"理由: {result['reason']}")
        print()
        print(f"最低资源: {result['resources']}")
        print(f"独立 DB: {result['db_advice']}")
        print(f"Redis: {result['redis_advice']}")
        print(f"地域: {result['region_advice']}")
    return result


if __name__ == '__main__':
    main()
