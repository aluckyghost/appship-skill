#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight.py — AppShip v0.4.1 / Step 3: Production Preflight

检查生产就绪度: 环境变量准备 / 健康检查端点 / 本地存储风险 / 持久化风险。

用法:
    python scripts/preflight.py /path/to/project [--json]
"""

import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    'node_modules', '.git', 'dist', 'build', 'out', '.next', '.nuxt',
    'venv', '.venv', '__pycache__', '.idea', '.vscode', 'vendor', '.cache',
}
SCAN_EXTS = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte'}
MAX_TEXT_BYTES = 512 * 1024

ENV_PATTERNS = [
    re.compile(r'process\.env\.([A-Za-z_][A-Za-z0-9_]*)'),
    re.compile(r'process\.env\[\s*[\'"]([A-Za-z_][A-Za-z0-9_]*)[\'"]\s*\]'),
    re.compile(r'import\.meta\.env\.([A-Z0-9_]+)'),
    re.compile(r'os\.environ\.get\(\s*[\'"]([A-Za-z_][A-Za-z0-9_]*)[\'"]'),
    re.compile(r'os\.environ\[\s*[\'"]([A-Za-z_][A-Za-z0-9_]*)[\'"]\s*\]'),
    re.compile(r'os\.getenv\(\s*[\'"]([A-Za-z_][A-Za-z0-9_]*)[\'"]'),
]

HEALTH_PATHS = ['/health', '/healthz', '/ping', '/api/health', '/healthz/', '/ready', '/readyz']
HEALTH_RE = re.compile(r'[\'"`](/health\w*|/api/health\w*|/ping|/ready\w*)[\'"`]')


def iter_code_files(root: Path):
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix not in SCAN_EXTS:
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        yield p


def read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
        if b'\x00' in data[:4096]:
            return ''
        return data[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore').lstrip('\ufeff')
    except OSError:
        return ''


def load_env_keys(root: Path) -> set:
    """从 .env / .env.example 收集已声明的变量名。"""
    keys = set()
    for name in ('.env', '.env.example', '.env.local', '.env.sample'):
        p = root / name
        if not p.is_file():
            continue
        for line in read_text(p).splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            keys.add(line.split('=')[0].strip())
    return keys


def preflight(root: Path) -> dict:
    checks = []

    # 1. 环境变量准备度
    referenced = set()
    has_code = False
    for p in iter_code_files(root):
        has_code = True
        text = read_text(p)
        for pat in ENV_PATTERNS:
            referenced.update(pat.findall(text))

    declared = load_env_keys(root)
    missing = sorted(referenced - declared) if referenced else []

    if not has_code:
        checks.append({'item': 'code_files', 'status': 'info', 'detail': '未发现可执行代码（纯静态项目）'})
    elif referenced:
        if missing:
            checks.append({'item': 'env_readiness', 'status': 'warn',
                           'detail': f'代码引用 {len(referenced)} 个环境变量，{len(missing)} 个未在 .env/.env.example 声明: {", ".join(missing[:10])}'})
        else:
            checks.append({'item': 'env_readiness', 'status': 'ok',
                           'detail': f'代码引用的 {len(referenced)} 个环境变量均已声明'})
        if not (root / '.env.example').is_file():
            checks.append({'item': 'env_example', 'status': 'warn', 'detail': '缺少 .env.example，建议生成环境变量模板'})
    else:
        checks.append({'item': 'env_readiness', 'status': 'ok', 'detail': '代码未引用环境变量'})

    # 2. 健康检查端点
    health_found = False
    for p in iter_code_files(root):
        if HEALTH_RE.search(read_text(p)):
            health_found = True
            break
    if has_code:
        checks.append({'item': 'health_endpoint', 'status': 'ok' if health_found else 'warn',
                       'detail': '发现健康检查端点' if health_found
                       else '未发现 /health 类端点，建议补充（Preview/生产探活需要）'})

    # 3. 本地存储 / SQLite 持久化风险
    sqlite_files = [p.name for p in root.rglob('*') if p.suffix in ('.db', '.sqlite', '.sqlite3')
                    and not any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1])]
    uploads_dir = (root / 'uploads').is_dir() or (root / 'static' / 'uploads').is_dir()

    if sqlite_files:
        checks.append({'item': 'sqlite_persistence', 'status': 'warn',
                       'detail': f'发现 SQLite 文件 {sqlite_files[:5]}，容器化后重启数据丢失，需挂载持久卷或改用云数据库'})
    if uploads_dir:
        checks.append({'item': 'local_uploads', 'status': 'warn',
                       'detail': '存在 uploads/ 本地上传目录，容器环境需迁移对象存储（OSS/COS）或持久卷'})

    # 4. 数据库依赖暗示
    req = root / 'requirements.txt'
    pkg = root / 'package.json'
    db_hint = False
    if req.is_file():
        db_hint = any(k in read_text(req).lower() for k in ('psycopg', 'pymysql', 'sqlalchemy', 'pymongo'))
    if pkg.is_file():
        t = read_text(pkg)
        db_hint = db_hint or any(k in t for k in ('mysql2', '"pg"', 'mongoose', 'prisma'))
    if db_hint:
        checks.append({'item': 'database', 'status': 'info',
                       'detail': '检测到数据库依赖，Preview 建议用临时数据库，Production 需云数据库（RDS 等）'})

    # 评分: ok=100, info 不扣分, warn 每项 -20
    warn_count = sum(1 for c in checks if c['status'] == 'warn')
    score = max(0, 100 - warn_count * 20)

    return {
        'project': root.name,
        'readiness_score': score,
        'checks': checks,
    }


def main():
    parser = argparse.ArgumentParser(description='AppShip Step 3: Production Preflight')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = preflight(root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"项目: {result['project']}  Production Readiness: {result['readiness_score']}/100")
        print()
        icon = {'ok': '✓', 'warn': '⚠', 'info': 'ℹ'}
        for c in result['checks']:
            print(f"  [{icon[c['status']]}] {c['item']}: {c['detail']}")
    return result


if __name__ == '__main__':
    main()
