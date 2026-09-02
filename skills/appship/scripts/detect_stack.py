#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_stack.py — AppShip v0.4.1 / Step 1: 识别项目技术栈

零第三方依赖，仅使用 Python 标准库。
扫描项目文件，识别框架/运行时/构建命令/服务依赖/Agent 框架/monorepo/
服务端口/CI/CD/目标市场暗示，结果可落盘为 project_profile.json。

用法:
    python scripts/detect_stack.py /path/to/project [--json] [--save]
"""

import argparse
import json
import sys
from pathlib import Path

# ---------- 常量 ----------

SKIP_DIRS = {
    'node_modules', '.git', '.svn', 'dist', 'build', 'out', '.next', '.nuxt',
    'venv', '.venv', 'env', '__pycache__', '.idea', '.vscode', 'target',
    'vendor', '.cache', 'coverage', '.pytest_cache', '.turbo', '.gradle',
    '.appship',
}
MAX_FILES = 3000
MAX_TEXT_BYTES = 512 * 1024  # 单文件最多读 512KB

JS_FRAMEWORKS = {
    'react': 'React', 'vue': 'Vue', 'svelte': 'Svelte', 'angular': 'Angular',
    'vite': 'Vite', 'next': 'Next.js', 'nuxt': 'Nuxt', 'astro': 'Astro',
    '@nestjs/core': 'NestJS', 'express': 'Express', 'koa': 'Koa', 'fastify': 'Fastify',
    'typescript': 'TypeScript', 'tailwindcss': 'TailwindCSS',
    '@remix-run/react': 'Remix', 'gatsby': 'Gatsby',
}
PY_FRAMEWORKS = {
    'fastapi': 'FastAPI', 'flask': 'Flask', 'django': 'Django',
    'tornado': 'Tornado', 'sanic': 'Sanic', 'starlette': 'Starlette',
    'uvicorn': 'Uvicorn', 'gunicorn': 'Gunicorn',
}
# Agent / LLM 应用框架（v0.5 P0 要求识别 Dify / LangGraph / Agent 项目）
JS_AGENT = {
    'langchain': 'LangChain(JS)', '@langchain/core': 'LangChain(JS)',
    '@langchain/langgraph': 'LangGraph(JS)', 'langgraph': 'LangGraph(JS)',
    '@crewjs/crewai': 'CrewAI(JS)', 'agno': 'Agno',
    '@mastra/core': 'Mastra', 'botbuilder': 'Bot Framework',
}
PY_AGENT = {
    'langchain': 'LangChain', 'langgraph': 'LangGraph', 'langgraph-checkpoint': 'LangGraph',
    'crewai': 'CrewAI', 'autogen': 'AutoGen', 'autogen-agentchat': 'AutoGen',
    'agno': 'Agno', 'phi': 'Phidata', 'llama-index': 'LlamaIndex',
    'dify-sdk': 'Dify SDK', 'openai-agents': 'OpenAI Agents',
    'semantic-kernel': 'Semantic Kernel', 'haystack': 'Haystack',
    'chatchat': 'Chatchat', 'langflow': 'LangFlow', 'flowise': 'Flowise',
}
DIFY_IMAGES = ('langgenius/dify-api', 'langgenius/dify-web', 'langgenius/dify-plugin')

JS_DB = {'pg', 'postgres', 'mysql2', 'better-sqlite3', 'mongoose', 'prisma', '@prisma/client', 'sequelize'}
PY_DB = {'sqlalchemy', 'pymongo', 'psycopg2', 'psycopg2-binary', 'pymysql', 'peewee', 'asyncpg', 'sqlmodel'}
CACHE = {'redis', 'ioredis'}
QUEUE = {'bullmq', 'bull', 'amqplib', 'kafkajs', 'nats', 'celery', 'rq', 'dramatiq'}
# MCP（Model Context Protocol）服务 SDK
PY_MCP = {'mcp', 'fastmcp'}
JS_MCP = {'@modelcontextprotocol/sdk', '@modelcontextprotocol/server-sdk'}
# CLI 框架（run_once 形态暗示）
PY_CLI = {'click', 'typer', 'fire', 'argparse'}
JS_CLI = {'commander', 'yargs', 'oclif', '@oclif/core', 'inquirer'}
AUTH = {'jsonwebtoken', 'passport', 'next-auth', '@auth/core', 'bcrypt', 'pyjwt', 'python-jose', 'passlib'}
PAYMENT = {'stripe', '@stripe/stripe-js', 'alipay-sdk', 'wechatpay-node-v3', 'alipay', 'paypal'}
EMAIL = {'nodemailer', '@sendgrid/mail', 'sendgrid'}
AI = {'openai', '@anthropic-ai/sdk', 'langchain', '@langchain/core', '@google/generative-ai', 'anthropic'}

LOCK_FILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'Pipfile.lock', 'uv.lock'}
SERVERLESS_FILES = {'vercel.json', 'netlify.toml', 'wrangler.toml'}
CI_FILES = {
    '.github/workflows': 'GitHub Actions', '.gitlab-ci.yml': 'GitLab CI',
    'Jenkinsfile': 'Jenkins', '.circleci': 'CircleCI',
    '.travis.yml': 'Travis CI', 'cloudbuild.yaml': 'Cloud Build',
    '.gitea/workflows': 'Gitea Actions', '.drone.yml': 'Drone CI',
}
# 目标市场暗示: 中国大陆特有 vs 海外特有
CN_HINTS = {'wechatpay-node-v3', 'alipay-sdk', 'alipay', 'wechat', 'weapp', 'wx-server-sdk',
            'tencentcloud', 'aliyun-python-sdk', 'dysmsapi'}
GLOBAL_HINTS = {'stripe', '@stripe/stripe-js', 'paypal', 'google-auth', 'passport-google-oauth',
                '@supabase/supabase-js', 'resend', 'posthog', 'mixpanel', 'plausible'}

PORT_PATTERNS = [
    # JS: app.listen(3000) / const PORT = 3000 / port: 3000
    r'\blisten\s*\(\s*(?:process\.env\.\w+\s*\|\|\s*)?(\d{2,5})\b',
    r'\bport\s*[:=]\s*(?:process\.env\.\w+\s*\|\|\s*)?["\']?(\d{2,5})["\']?',
    # Python: uvicorn.run(app, port=8000) / app.run(port=5000)
    r'\bport\s*=\s*(?:int\s*\(\s*os\.environ[^)]*\)\s*or\s*)?(\d{2,5})\b',
    r'\bPORT\s*=\s*(?:int\s*\(\s*[^)]*\)\s*or\s*)?(\d{2,5})\b',
    r'\blisten\s+([0-9]{2,5})\b',
]


# ---------- 工具函数 ----------

def iter_project_files(root: Path):
    """遍历项目文件，跳过依赖/构建目录与锁文件。"""
    count = 0
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if p.name in LOCK_FILES:
            continue
        rel = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel[:-1]):
            continue
        count += 1
        if count > MAX_FILES:
            break
        yield p


def is_project_dir(root: Path) -> bool:
    """目录里是否存在真实项目文件（排除 .appship/.git/node_modules 等生成物）。

    用于入口守卫: 空目录/只有生成物的目录直接报错，不产出无意义报告。
    """
    return any(True for _ in iter_project_files(root))


def read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
        if b'\x00' in data[:4096]:  # 二进制文件
            return ''
        text = data[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore')
        return text.lstrip('\ufeff')  # 去除 BOM
    except OSError:
        return ''


def read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='ignore'))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------- 检测逻辑 ----------

def detect_js(root: Path, result: dict):
    pkg_path = root / 'package.json'
    if not pkg_path.is_file():
        return False
    pkg = read_json_file(pkg_path)
    deps = {}
    deps.update(pkg.get('dependencies') or {})
    deps.update(pkg.get('devDependencies') or {})
    scripts = pkg.get('scripts') or {}

    result['language'] = 'javascript'
    result['package_manager'] = 'npm'
    if (root / 'yarn.lock').exists():
        result['package_manager'] = 'yarn'
    elif (root / 'pnpm-lock.yaml').exists():
        result['package_manager'] = 'pnpm'

    all_deps = set(deps)

    for dep, name in JS_FRAMEWORKS.items():
        if dep in deps:
            result['frameworks'].append(name)
    for dep, name in JS_AGENT.items():
        if dep in deps:
            result['frameworks'].append(name)
            result['hints']['agent'] = True

    result['services']['database'] = sorted(all_deps & JS_DB)
    result['services']['cache'] = bool(all_deps & CACHE)
    result['services']['queue'] = bool(all_deps & QUEUE)
    result['services']['storage'] = bool(all_deps & {'multer', 'formidable'} or (root / 'uploads').is_dir())
    result['features']['auth'] = bool(all_deps & AUTH)
    result['features']['payment'] = bool(all_deps & PAYMENT)
    result['features']['email'] = bool(all_deps & EMAIL)
    result['features']['ai'] = bool(all_deps & AI)

    # MCP / CLI 暗示（JS）
    if all_deps & JS_MCP:
        result['hints']['mcp'] = True
    if pkg.get('bin') or (all_deps & JS_CLI):
        result['hints']['cli'] = True

    # 目标市场暗示
    if all_deps & CN_HINTS:
        result['target_market'] = 'china'
    elif all_deps & GLOBAL_HINTS:
        result['target_market'] = 'global'
    else:
        result['target_market'] = 'unknown'

    result['build_command'] = scripts.get('build', '')
    result['start_command'] = scripts.get('start', '')
    result['dev_command'] = scripts.get('dev', '')

    # Next.js 静态导出检测
    has_next = 'next' in deps
    if has_next:
        cfg = ''
        for f in ('next.config.js', 'next.config.mjs', 'next.config.ts'):
            if (root / f).is_file():
                cfg = read_text(root / f)
                break
        result['hints']['ssr'] = 'output:\'export\'' not in cfg.replace(' ', '') and 'output:"export"' not in cfg.replace(' ', '')

    # 运行形态：只有前端框架 + 无服务端框架 → 可静态构建
    server_side = has_next and result['hints']['ssr'] or bool(all_deps & {'express', 'koa', 'fastify', '@nestjs/core'})
    frontend_only = bool(all_deps & {'react', 'vue', 'svelte', 'astro'}) and not server_side
    if frontend_only or (has_next and not result['hints']['ssr']):
        result['runtime'] = 'static_build'
    else:
        result['runtime'] = 'node'
    return True


def detect_python(root: Path, result: dict):
    req = root / 'requirements.txt'
    pyproject = root / 'pyproject.toml'
    req_text = ''
    if req.is_file():
        req_text = read_text(req)
    elif pyproject.is_file():
        req_text = read_text(pyproject)
    else:
        # 无依赖文件但有 .py 文件 → 纯 Python 脚本
        py_files = [p for p in iter_project_files(root) if p.suffix == '.py']
        if py_files:
            result['language'] = 'python'
            result['runtime'] = 'python'
            result['package_manager'] = 'pip'
        return bool(py_files)

    pkgs = set()
    for line in req_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('['):
            continue
        name = line.split('==')[0].split('>=')[0].split('<=')[0].split('~=')[0].split('>')[0].split('<')[0].split('!=')[0].strip().lower()
        if name:
            pkgs.add(name)

    result['language'] = 'python'
    result['package_manager'] = 'pip'
    result['dependencies'] = sorted(pkgs)[:50]

    for pkg, name in PY_FRAMEWORKS.items():
        if pkg in pkgs:
            result['frameworks'].append(name)
    for pkg, name in PY_AGENT.items():
        if pkg in pkgs:
            result['frameworks'].append(name)
            result['hints']['agent'] = True

    result['services']['database'] = sorted(pkgs & PY_DB)
    result['services']['cache'] = 'redis' in pkgs
    result['services']['queue'] = bool(pkgs & QUEUE)
    result['services']['storage'] = 'boto3' in pkgs or 'oss2' in pkgs or (root / 'uploads').is_dir()
    result['features']['auth'] = bool(pkgs & AUTH)
    result['features']['payment'] = bool(pkgs & PAYMENT)
    result['features']['email'] = bool(pkgs & {'sendgrid', 'emails'}) or 'smtplib' in req_text
    result['features']['ai'] = bool(pkgs & AI)

    # MCP / CLI 暗示（Python）
    if pkgs & PY_MCP:
        result['hints']['mcp'] = True
    if pkgs & PY_CLI:
        result['hints']['cli'] = True
    elif pyproject.is_file() and '[project.scripts]' in req_text:
        result['hints']['cli'] = True

    # 目标市场暗示
    if pkgs & CN_HINTS:
        result['target_market'] = 'china'
    elif pkgs & GLOBAL_HINTS:
        result['target_market'] = 'global'
    else:
        result['target_market'] = 'unknown'

    if pkgs & {'fastapi', 'flask', 'django', 'tornado', 'sanic', 'starlette'}:
        result['runtime'] = 'python_service'
    else:
        result['runtime'] = 'python'
    return True


def detect_dify(root: Path, result: dict) -> bool:
    """识别 Dify 自部署项目（docker-compose 含 langgenius 镜像）。"""
    for f in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml'):
        p = root / f
        if not p.is_file():
            continue
        t = read_text(p)
        if any(img in t for img in DIFY_IMAGES):
            result['frameworks'].append('Dify')
            result['hints']['agent'] = True
            result['hints']['dify'] = True
            return True
    return False


def detect_monorepo(root: Path, result: dict):
    """monorepo 结构识别（v0.5 P0 要求）。"""
    if (root / 'pnpm-workspace.yaml').is_file() or (root / 'turbo.json').is_file() \
            or (root / 'lerna.json').is_file() or (root / 'nx.json').is_file():
        result['hints']['monorepo'] = True
        return
    apps = (root / 'apps').is_dir()
    packages = (root / 'packages').is_dir()
    if apps and packages:
        result['hints']['monorepo'] = True


def detect_ci(root: Path, result: dict):
    """CI/CD 识别。"""
    for name, ci in CI_FILES.items():
        if (root / name).exists():
            result['ci'] = ci
            return
    result['ci'] = None


def detect_ports(root: Path, result: dict):
    """从代码/配置中推断服务监听端口。"""
    import re as _re
    ports = set()
    for p in iter_project_files(root):
        if p.suffix not in ('.js', '.mjs', '.cjs', '.ts', '.py', '.json', '.yaml', '.yml', '.toml', '.env', '.example'):
            if p.name not in ('Dockerfile', '.env.example'):
                continue
        text = read_text(p)
        if not text:
            continue
        for pat in PORT_PATTERNS:
            for m in _re.finditer(pat, text):
                try:
                    port = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                if 1024 <= port <= 65535:  # 忽略常见非端口数字
                    ports.add(port)
    # 排除明显不是端口的（如 3000 以外的版本号误报难判，保留常见 Web 端口优先）
    result['ports'] = sorted(ports)[:8]


def detect_upload_dirs(root: Path, result: dict):
    """本地上传/持久化目录识别。"""
    dirs = []
    for name in ('uploads', 'upload', 'static/uploads', 'media', 'data', 'storage'):
        if (root / name).is_dir():
            dirs.append(name)
    result['local_persistent_dirs'] = dirs


def detect(root: Path) -> dict:
    result = {
        'project': root.name,
        'language': 'unknown',
        'runtime': 'unknown',  # static_html | static_build | node | python | python_service
        'frameworks': [],
        'package_manager': None,
        'build_command': None,
        'start_command': None,
        'dev_command': None,
        'ports': [],
        'ci': None,
        'target_market': 'unknown',  # china | global | unknown
        'services': {'database': [], 'cache': False, 'queue': False, 'storage': False},
        'features': {'auth': False, 'payment': False, 'email': False, 'ai': False},
        'existing_docker': {
            'dockerfile': (root / 'Dockerfile').is_file(),
            'compose': any((root / f).is_file() for f in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml')),
        },
        'local_persistent_dirs': [],
        'hints': {'ssr': False, 'serverless': False, 'workers': False, 'agent': False,
                  'dify': False, 'monorepo': False, 'mcp': False, 'cli': False},
        'file_count': 0,
    }

    found = detect_js(root, result)
    if not found:
        found = detect_python(root, result)

    # 纯静态 HTML（无 package.json / 无 Python）
    if not found:
        html_files = [p for p in iter_project_files(root) if p.suffix == '.html']
        result['file_count'] = len(list(iter_project_files(root)))
        if html_files:
            result['language'] = 'html'
            result['runtime'] = 'static_html'
        detect_monorepo(root, result)
        return result

    detect_dify(root, result)
    detect_monorepo(root, result)
    detect_ci(root, result)
    detect_upload_dirs(root, result)

    # serverless 暗示
    result['hints']['serverless'] = any((root / f).is_file() for f in SERVERLESS_FILES)

    # worker 暗示
    result['hints']['workers'] = result['services']['queue'] or bool(list(root.rglob('*worker*'))) and result['runtime'] != 'static_build'

    detect_ports(root, result)

    files = list(iter_project_files(root))
    result['file_count'] = len(files)
    return result


def profile_path(root: Path) -> Path:
    return root / '.appship' / 'project_profile.json'


def save_profile(root: Path, result: dict) -> Path:
    """落盘 project_profile.json（v0.5 P0 输出物）。"""
    out = profile_path(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return out


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(description='AppShip Step 1: 识别项目技术栈')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--save', action='store_true', help='落盘 .appship/project_profile.json')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = detect(root)

    if args.save:
        p = save_profile(root, result)
        if not args.json:
            print(f'已写入 {p}')

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"项目: {result['project']}")
        print(f"语言: {result['language']}  运行形态: {result['runtime']}")
        print(f"框架: {', '.join(sorted(set(result['frameworks']))) or '无'}")
        print(f"包管理: {result['package_manager'] or '无'}  CI: {result['ci'] or '无'}")
        print(f"构建: {result['build_command'] or '无'}  启动: {result['start_command'] or '无'}")
        print(f"端口: {', '.join(map(str, result['ports'])) or '未识别'}")
        print(f"目标市场暗示: {result['target_market']}")
        print(f"服务依赖: {json.dumps(result['services'], ensure_ascii=False)}")
        print(f"功能暗示: {json.dumps(result['features'], ensure_ascii=False)}")
        print(f"已有 Docker: {json.dumps(result['existing_docker'])}")
        hints = [k for k, v in result['hints'].items() if v]
        print(f"特殊形态: {', '.join(hints) or '无'}")
    return result


if __name__ == '__main__':
    main()
