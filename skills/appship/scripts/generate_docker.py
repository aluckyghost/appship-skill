#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_docker.py — AppShip v0.4.1 / Step 5: 生成部署配置

仅对容器类项目生成 Dockerfile / .dockerignore / docker-compose.yml。
静态项目（STATIC / STATIC_PLUS_FUNCTION）默认不生成 Docker。
默认 dry-run 输出到屏幕，--write 落盘（不覆盖已有文件，除非 --force）。

用法:
    python scripts/generate_docker.py /path/to/project [--write] [--force]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from deployment_decision import decide

DOCKERIGNORE = """node_modules
.git
.env
.env.*
*.pem
*.key
__pycache__
*.pyc
venv
.venv
dist
build
uploads
*.db
*.sqlite
.DS_Store
README.md
"""

PY_ENTRY_HINTS = ['main.py', 'app.py', 'server.py', 'run.py', 'application.py', 'manage.py']


def find_python_entry(root: Path):
    for name in PY_ENTRY_HINTS:
        if (root / name).is_file():
            return name
    return 'main.py'


def node_dockerfile(stack: dict) -> str:
    has_build = bool(stack.get('build_command'))
    is_next = any(f == 'Next.js' for f in stack['frameworks'])
    if is_next:
        return f"""# syntax=docker/dockerfile:1
# Next.js SSR — AppShip 生成
FROM node:20-alpine AS deps
WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://localhost:3000/api/health || exit 1
CMD ["node", "server.js"]
"""
    if has_build:
        return """# syntax=docker/dockerfile:1
# Node 服务（含构建步骤）— AppShip 生成
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist ./dist
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://localhost:3000/health || exit 1
CMD ["npm", "start"]
"""
    return """# syntax=docker/dockerfile:1
# Node API 服务 — AppShip 生成
FROM node:20-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev
COPY . .
RUN addgroup -S app && adduser -S app -G app
USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://localhost:3000/health || exit 1
CMD ["npm", "start"]
"""


def python_dockerfile(root: Path, stack: dict) -> str:
    entry = find_python_entry(root)
    is_django = any(f == 'Django' for f in stack['frameworks'])
    if is_django:
        cmd = f'CMD ["gunicorn", "{entry.replace(".py", "")}.wsgi:application", "--bind", "0.0.0.0:8000"]'
    else:
        app_var = 'app' if 'app = FastAPI' in _peek(root / entry) or 'app = Flask' in _peek(root / entry) else 'app'
        cmd = f'CMD ["uvicorn", "{entry.replace(".py", "")}:{app_var}", "--host", "0.0.0.0", "--port", "8000"]'
    return f"""# syntax=docker/dockerfile:1
# Python 服务 — AppShip 生成
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN useradd -m appuser
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" || exit 1
{cmd}
"""


def _peek(p: Path) -> str:
    try:
        return p.read_text(encoding='utf-8', errors='ignore')[:8000]
    except OSError:
        return ''


def compose_yaml(dtype: str) -> str:
    return f"""# docker-compose.yml — AppShip 生成（{dtype}）
services:
  app:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 512M
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
"""


def generate(root: Path, write: bool = False, force: bool = False) -> dict:
    decision = decide(root)
    dtype = decision['type']
    stack = decision['stack']

    if dtype in ('STATIC', 'STATIC_HOSTING', 'STATIC_PLUS_FUNCTION'):
        return {
            'decision': dtype,
            'skip_reason': '静态项目不需要 Docker，使用 OSS/COS + CDN 静态托管即可（如确需容器请手动生成）',
            'files': {},
        }

    files = {}
    if stack['runtime'] in ('node',):
        files['Dockerfile'] = node_dockerfile(stack)
    else:
        files['Dockerfile'] = python_dockerfile(root, stack)
    files['.dockerignore'] = DOCKERIGNORE
    if dtype == 'COMPOSE_OR_DEDICATED':
        files['docker-compose.yml'] = compose_yaml(dtype)

    written = {}
    if write:
        for name, content in files.items():
            target = root / name
            if target.exists() and not force:
                written[name] = 'skipped (已存在, 用 --force 覆盖)'
                continue
            target.write_text(content, encoding='utf-8')
            written[name] = 'written'
    else:
        written = {name: 'dry-run' for name in files}

    return {'decision': dtype, 'files': files, 'written': written}


def main():
    parser = argparse.ArgumentParser(description='AppShip Step 5: 生成部署配置')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--write', action='store_true', help='写入项目目录（默认 dry-run）')
    parser.add_argument('--force', action='store_true', help='覆盖已有文件')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = generate(root, write=args.write, force=args.force)

    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != 'files'}, ensure_ascii=False, indent=2))
    else:
        print(f"部署类型: {result['decision']}")
        if 'skip_reason' in result:
            print(f"跳过: {result['skip_reason']}")
        else:
            for name, status in result['written'].items():
                print(f"  {name}: {status}")
            if not args.write:
                print()
                for name, content in result['files'].items():
                    print(f'===== {name} =====')
                    print(content)
    return result


if __name__ == '__main__':
    main()
