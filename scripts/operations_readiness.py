#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
operations_readiness.py — AppShip v0.4.1 / Step 5.6: 运维就绪度检查

检查长期运行必需项（v0.5 §8）:
  监控 / Health Check / 日志 / Alert / 数据库备份 / 文件备份 /
  发布流程 / 回滚 / SSL 续期 / 磁盘监控 / CPU 内存监控 / 安全更新 / 故障恢复

静态启发式检查: 检测 Dockerfile 健康检查声明、compose restart 策略、
备份脚本、监控 SDK 等。输出缺口清单。

用法:
    python scripts/operations_readiness.py /path/to/project [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from detect_stack import detect, read_text, iter_project_files

SKIP_DIRS = {'node_modules', '.git', 'dist', 'build', 'out', '.next', '__pycache__', 'venv', '.venv'}


def operations(root: Path) -> dict:
    stack = detect(root)
    deps = set()
    # 依赖集合（从 detect 的 dependencies 或 package.json）
    pkg = root / 'package.json'
    if pkg.is_file():
        try:
            p = json.loads(pkg.read_text(encoding='utf-8', errors='ignore'))
            deps.update((p.get('dependencies') or {}).keys())
        except json.JSONDecodeError:
            pass
    deps.update(stack.get('dependencies') or [])

    gaps = []
    ok = []

    # 类型感知: 静态托管无自管服务器/服务端日志，监控口径切换为"可用性拨测"
    is_static = stack.get('runtime') in ('static_html', 'static_build')

    # 1. Health Check
    if stack.get('runtime') in ('static_html', 'static_build'):
        ok.append({'item': 'health_check', 'detail': '静态站无需进程探活（CDN 层可用拨测）'})
    elif stack.get('existing_docker', {}).get('dockerfile'):
        df = read_text(root / 'Dockerfile')
        if 'HEALTHCHECK' in df.upper():
            ok.append({'item': 'health_check', 'detail': 'Dockerfile 已声明 HEALTHCHECK'})
        else:
            gaps.append({'item': 'health_check', 'need': 'Dockerfile 缺 HEALTHCHECK 指令',
                         'hint': '容器编排器依赖它自动重启故障实例'})
    else:
        gaps.append({'item': 'health_check', 'need': '无健康检查端点声明',
                     'hint': '提供 /health 端点，负载均衡/监控依赖它'})

    # 2. restart 策略（compose 项目）
    compose_file = None
    for f in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'):
        if (root / f).is_file():
            compose_file = root / f
            break
    if compose_file:
        ct = read_text(compose_file)
        if 'restart:' in ct or 'restart :' in ct:
            ok.append({'item': 'restart_policy', 'detail': 'compose 已声明 restart 策略'})
        else:
            gaps.append({'item': 'restart_policy', 'need': 'compose 服务缺 restart: unless-stopped',
                         'hint': '服务器重启/进程崩溃后服务不会自动拉起'})

    # 3. 监控 SDK
    MONITOR = {'sentry', '@sentry/node', '@sentry/nextjs', 'sentry-sdk', 'prometheus-client',
               'prom-client', 'datadog', '@datadog/browser-rum', 'otel', '@opentelemetry/api'}
    if deps & MONITOR:
        ok.append({'item': 'monitoring', 'detail': f"已接入监控 SDK（{', '.join(sorted(deps & MONITOR)[:3])}）"})
    elif is_static:
        gaps.append({'item': 'uptime_monitor', 'need': '无可用性拨测（uptime 监控）',
                     'hint': 'UptimeRobot 免费档即可；静态站不需要 APM'})
    else:
        gaps.append({'item': 'monitoring', 'need': '无错误监控/APM',
                     'hint': 'Sentry 免费档足够起步；无监控 = 用户先于你发现故障'})

    # 4. 日志方案
    has_log_config = False
    if compose_file and 'logging:' in read_text(compose_file):
        has_log_config = True
    if deps & {'winston', 'pino', 'loguru', 'structlog'}:
        has_log_config = True
    if is_static:
        ok.append({'item': 'logging', 'detail': '静态托管无服务端日志（CDN 访问日志可选）'})
    elif has_log_config:
        ok.append({'item': 'logging', 'detail': '检测到日志库/日志配置'})
    else:
        gaps.append({'item': 'logging', 'need': '无结构化日志方案',
                     'hint': 'stdout JSON 日志 + 云日志服务采集即可，无需 ELK'})

    # 5. 数据库备份
    if stack.get('services', {}).get('database'):
        has_backup = False
        for p in iter_project_files(root):
            if 'backup' in p.name.lower() or 'dump' in p.name.lower():
                has_backup = True
                break
        if has_backup:
            ok.append({'item': 'db_backup', 'detail': '检测到备份脚本'})
        else:
            gaps.append({'item': 'db_backup', 'need': '有数据库依赖但无备份脚本',
                         'hint': '最低标准: 每日 mysqldump/pg_dump 到对象存储，保留 7 天'})
    else:
        ok.append({'item': 'db_backup', 'detail': '无数据库依赖，跳过'})

    # 6. 发布/回滚流程
    if stack.get('ci'):
        ok.append({'item': 'release', 'detail': f"已有 CI（{stack['ci']}）"})
    else:
        gaps.append({'item': 'release', 'need': '无 CI/CD，部署靠手动',
                     'hint': '最简: GitHub Actions → SSH 到服务器 → docker compose up -d；回滚 = 上一镜像 tag'})

    # 7. 磁盘/资源监控（服务器侧，静态检查只能提示；静态托管无自管服务器）
    if is_static:
        ok.append({'item': 'disk_cpu_monitor', 'detail': '无自管服务器（托管平台兜底）'})
    else:
        gaps.append({'item': 'disk_cpu_monitor', 'need': '服务器磁盘/CPU/内存监控',
                     'hint': '云监控免费基础告警即可：磁盘 >80%、CPU >90% 持续 5 分钟报警'})

    # 8. SSL 续期
    if stack.get('runtime') in ('static_html', 'static_build'):
        ok.append({'item': 'ssl_renew', 'detail': '静态托管 SSL 由平台自动续期'})
    else:
        gaps.append({'item': 'ssl_renew', 'need': 'HTTPS 证书自动续期',
                     'hint': 'Caddy 自动证书（推荐）或 certbot --renew 定时任务'})

    # 9. 故障恢复
    if is_static:
        ok.append({'item': 'dr_plan', 'detail': '静态资产可重新上传恢复，风险低'})
    else:
        gaps.append({'item': 'dr_plan', 'need': '无故障恢复预案',
                     'hint': '写一页纸即可: 数据在哪、怎么恢复、谁能操作、演练一次'})

    score = round(100 * len(ok) / max(1, len(ok) + len(gaps)))

    return {
        'project': stack['project'],
        'ops_score': score,
        'ok': ok,
        'gaps': gaps,
    }


def main():
    parser = argparse.ArgumentParser(description='AppShip: 运维就绪度检查')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = operations(root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"项目: {result['project']}  运维就绪度: {result['ops_score']}%")
        print()
        print(f"已具备（{len(result['ok'])} 项）:")
        for o in result['ok']:
            print(f"  ✓ {o['item']}: {o['detail']}")
        print()
        print(f"缺口（{len(result['gaps'])} 项）:")
        for i, g in enumerate(result['gaps'], 1):
            print(f"  {i}. {g['item']}: {g['need']}")
            print(f"     提示: {g['hint']}")
    return result


if __name__ == '__main__':
    main()
