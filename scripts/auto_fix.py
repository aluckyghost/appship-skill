#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_fix.py — AppShip v0.4.1 / Safe Auto Fix

只做机械、可逆、低风险的修复（v0.5 §9.2）:
  1. 生成 .env.example（从代码中提取的环境变量引用，不含值）
  2. 更新 .gitignore（确保 .env / node_modules / __pycache__ 等在列）
  3. 显式关闭明显 Debug 配置（DEBUG = True → False，先备份）
不做: 修改业务逻辑、删除文件、改依赖版本。

每次修复前自动备份原文件到 .appship/backup/，可一键恢复。

用法:
    python scripts/auto_fix.py /path/to/project [--dry-run]
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from preflight import ENV_PATTERNS, iter_code_files, read_text

BACKUP_DIR = '.appship/backup'

GITIGNORE_ENSURE = {
    '.env': '# 环境变量（含密钥）',
    '.env.local': '',
    'node_modules/': '',
    '__pycache__/': '',
    'venv/': '',
    '.venv/': '',
    'dist/': '',
    'build/': '',
    '.appship/': '',
}


def backup(root: Path, rel: str) -> Path | None:
    src = root / rel
    if not src.is_file():
        return None
    bdir = root / BACKUP_DIR / datetime.now().strftime('%Y%m%d_%H%M%S')
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / rel.replace('/', '__')
    shutil.copy2(src, dst)
    return dst


def fix_env_example(root: Path, dry: bool) -> dict:
    """生成 .env.example（只含变量名，值留空）。"""
    referenced = set()
    for p in iter_code_files(root):
        text = read_text(p)
        for pat in ENV_PATTERNS:
            referenced.update(pat.findall(text))

    if not referenced:
        return {'fix': 'env_example', 'action': 'skip', 'detail': '未发现环境变量引用'}

    target = root / '.env.example'
    if target.is_file():
        existing = set()
        for line in read_text(target).splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                existing.add(line.split('=')[0].strip())
        missing = sorted(referenced - existing)
        if not missing:
            return {'fix': 'env_example', 'action': 'skip', 'detail': '.env.example 已覆盖全部变量'}
        content = read_text(target).rstrip() + '\n' + '\n'.join(f'{k}=' for k in missing) + '\n'
    else:
        content = '# AppShip 自动生成: 环境变量模板（值请自行填写，勿提交真实密钥）\n'
        content += '\n'.join(f'{k}=' for k in sorted(referenced)) + '\n'

    if not dry:
        backup(root, '.env.example')
        target.write_text(content, encoding='utf-8')
    return {'fix': 'env_example', 'action': 'create' if not target.is_file() else 'update',
            'detail': f'写入 {len(referenced)} 个变量模板 → .env.example'}


def fix_gitignore(root: Path, dry: bool) -> dict:
    """补全 .gitignore。"""
    gi = root / '.gitignore'
    current = read_text(gi) if gi.is_file() else ''
    current_lines = {l.strip().rstrip('/') for l in current.splitlines() if l.strip() and not l.startswith('#')}

    missing = []
    for entry, _comment in GITIGNORE_ENSURE.items():
        if entry.strip().rstrip('/') not in current_lines:
            missing.append(entry)

    if not missing:
        return {'fix': 'gitignore', 'action': 'skip', 'detail': '.gitignore 已完整'}

    if not dry:
        backup(root, '.gitignore')
        with open(gi, 'a', encoding='utf-8') as f:
            f.write('\n# AppShip auto-fix\n')
            for m in missing:
                f.write(f'{m}\n')
    return {'fix': 'gitignore', 'action': 'update',
            'detail': f'追加 {len(missing)} 条: {", ".join(missing)}'}


def fix_debug(root: Path, dry: bool) -> dict:
    """关闭明显 Debug 配置（DEBUG = True → DEBUG = False）。只改配置类 .py 文件。"""
    changed = []
    candidates = []
    for name in ('settings.py', 'config.py', 'settings/__init__.py', 'config/settings.py'):
        p = root / name
        if p.is_file():
            candidates.append(p)
    # 也扫 settings/ 目录
    sdir = root / 'settings'
    if sdir.is_dir():
        candidates.extend(p for p in sdir.glob('*.py'))

    for p in candidates:
        text = read_text(p)
        new_text, n = re.subn(r'(?m)^(\s*DEBUG\s*=\s*)True(\s*)$', r'\1False\2', text)
        if n > 0:
            if not dry:
                backup(root, str(p.relative_to(root)))
                p.write_text(new_text, encoding='utf-8')
            changed.append(f'{p.relative_to(root)} ({n} 处)')

    if not changed:
        return {'fix': 'debug_off', 'action': 'skip', 'detail': '未发现 DEBUG = True'}
    return {'fix': 'debug_off', 'action': 'update', 'detail': f'关闭 Debug: {"; ".join(changed)}'}


def auto_fix(root: Path, dry: bool = False) -> dict:
    results = [
        fix_env_example(root, dry),
        fix_gitignore(root, dry),
        fix_debug(root, dry),
    ]
    return {
        'project': root.name,
        'dry_run': dry,
        'backup_dir': f'{BACKUP_DIR}/<timestamp>/',
        'results': results,
        'changed': sum(1 for r in results if r['action'] != 'skip'),
    }


def main():
    parser = argparse.ArgumentParser(description='AppShip: 安全自动修复（低风险可逆）')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--dry-run', action='store_true', help='只预览不写盘')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = auto_fix(root, args.dry_run)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = '[预览] ' if args.dry_run else ''
        print(f"{mode}自动修复完成: {result['changed']} 项变更（备份在 {result['backup_dir']}）")
        for r in result['results']:
            print(f"  [{r['action']:7}] {r['fix']}: {r['detail']}")
    return result


if __name__ == '__main__':
    main()
