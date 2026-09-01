#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
security_scan.py — AppShip v0.4.1 / Step 2: Security Gate（上线前安全门）

零第三方依赖。检测 Secret / 明文密码 / Debug / 危险配置 / 隐私数据 / 危险容器配置 /
管理接口无鉴权 / 任意命令执行入口 / 测试账号残留。
严重级: CRITICAL(阻断) / HIGH(须修复) / MEDIUM(Production 前处理) / LOW(建议)

用法:
    python scripts/security_scan.py /path/to/project [--json] [--save]
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------- 常量 ----------

SKIP_DIRS = {
    'node_modules', '.git', '.svn', 'dist', 'build', 'out', '.next', '.nuxt',
    'venv', '.venv', 'env', '__pycache__', '.idea', '.vscode', 'target',
    'vendor', '.cache', 'coverage', '.pytest_cache', '.appship',
}
MAX_FILES = 5000
MAX_TEXT_BYTES = 512 * 1024
LOCK_FILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'Pipfile.lock', 'uv.lock'}

# 可扫描的扩展名（代码/配置/文本）
SCAN_EXTS = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte',
    '.html', '.htm', '.css', '.scss', '.less', '.json', '.yml', '.yaml',
    '.toml', '.ini', '.cfg', '.conf', '.env', '.sh', '.bash', '.zsh',
    '.md', '.txt', '.xml', '.sql', '.rb', '.go', '.java', '.php',
}
SCAN_NAMES = {'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml', '.env'}
SECRET_FILE_EXTS = {'.pem', '.key', '.pfx', '.p12'}

# 占位符白名单（不视为真实 Secret）
PLACEHOLDER_WORDS = ('your', 'xxx', 'xxxx', 'example', 'changeme', 'placeholder',
                     'sample', 'dummy', 'todo', 'fixme', '<', '{', '***', 'xxxxx')

# Secret 检测规则: (id, 正则, 严重级, 说明)
SECRET_RULES = [
    ('private_key', r'-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----',
     'CRITICAL', 'Private Key 明文出现'),
    ('aliyun_access_key', r'\bLTAI[A-Za-z0-9]{12,24}\b', 'CRITICAL', '疑似阿里云 AccessKey ID'),
    ('aws_access_key', r'\bAKIA[0-9A-Z]{16}\b', 'CRITICAL', '疑似 AWS AccessKey ID'),
    ('openai_api_key', r'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b', 'CRITICAL', '疑似 OpenAI/Anthropic API Key'),
    ('github_token', r'\bgh[pousr]_[A-Za-z0-9]{30,50}\b', 'CRITICAL', '疑似 GitHub Token'),
    ('slack_token', r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b', 'CRITICAL', '疑似 Slack Token'),
    ('jwt_token', r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b', 'HIGH', '疑似真实 JWT Token'),
    ('db_url_with_password', r'\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mariadb|amqp)://[^\s"\'<>]+:[^\s"\'<>@]+@',
     'CRITICAL', '数据库连接串内嵌明文密码'),
]

# 通用键值对硬编码: (password|secret|token|api_key...) = "value"
GENERIC_SECRET = re.compile(
    r'(?i)\b(password|passwd|pwd|secret|secret[_-]?key|secret[_-]?id|token|api[_-]?key|apikey|'
    r'access[_-]?key|access[_-]?key[_-]?secret|client[_-]?secret|app[_-]?secret|admin[_-]?pass(?:word)?)\b'
    r'\s*[:=]\s*["\']([^"\'\s]{6,})["\']'
)

WEAK_PASSWORDS = re.compile(
    r'(?i)\b(?:admin|root|test|user)\s*/\s*(?:admin|root|123456|password|test)\b'
)

DEBUG_RULES = [
    ('py_debug_true', re.compile(r'(?i)^\s*DEBUG\s*=\s*True\s*$', re.M), 'HIGH', '生产配置 DEBUG=True'),
    ('flask_debug', re.compile(r'\.run\([^)]*debug\s*=\s*True'), 'HIGH', 'Flask debug=True'),
    ('js_debug', re.compile(r'(?i)["\']?debug["\']?\s*[:=]\s*(?:true|["\']true["\']|1)\b'), 'MEDIUM', '前端/配置 debug 开启'),
]

CORS_RULES = [
    # wildcard + credentials 组合 = HIGH（v0.5 §4.2 高危项）
    ('cors_wildcard_credentials', re.compile(
        r'(?i)allow[-_]credentials["\']?\s*[:,]\s*(?:true|["\']true["\'])'), 'HIGH', 'CORS credentials 开启（若配合通配来源则高危）'),
    ('cors_wildcard', re.compile(r'(?i)access-control-allow-origin["\']?\s*[:,]\s*["\']?\*'), 'MEDIUM', 'CORS 允许任意来源 (*)'),
    ('fastapi_cors_wildcard', re.compile(r'(?i)allow_origins\s*=\s*\[\s*\*?\s*["\']\*["\']'), 'MEDIUM', 'FastAPI CORS 通配'),
    ('fastapi_cors_credentials', re.compile(r'(?i)allow_credentials\s*=\s*True'), 'MEDIUM', 'FastAPI CORS allow_credentials=True（确认来源非通配）'),
    ('js_cors_wildcard', re.compile(r'(?i)cors\(\s*\{[^}]*origin\s*:\s*["\']\*["\']'), 'MEDIUM', 'JS CORS 通配'),
]

DANGEROUS_CODE_RULES = [
    ('curl_pipe_sh', re.compile(r'curl\s+[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh'), 'HIGH', 'curl 管道执行 shell'),
    ('chmod_777', re.compile(r'chmod\s+777'), 'MEDIUM', 'chmod 777'),
    ('shell_true', re.compile(r'\bshell\s*=\s*True'), 'MEDIUM', 'subprocess shell=True 命令注入风险'),
    ('os_system', re.compile(r'\bos\.system\s*\('), 'MEDIUM', 'os.system 调用'),
    ('eval_call', re.compile(r'(?<!def\s)\beval\s*\('), 'MEDIUM', 'eval 动态执行'),
    ('sql_concat', re.compile(r'(?:f["\'](?:SELECT|INSERT|UPDATE|DELETE)[^\n]{0,60}\{)|(?:["\'](?:SELECT|WHERE)[^\n]{0,60}["\']\s*\+)'), 'MEDIUM', 'SQL 拼接（疑似注入风险）'),
    # 任意命令执行入口（v0.5 §4.1 CRITICAL：用户输入直通命令执行）
    ('exec_user_input', re.compile(
        r'(?i)\b(?:exec|execute|run|popen|system)\s*\(\s*(?:request|req)\.'), 'CRITICAL', '用户输入直通命令执行（任意命令执行入口）'),
]

# 管理接口无鉴权 / 测试账号残留（v0.5 §4.2）
ADMIN_ROUTE_RULES = [
    # Flask/FastAPI 路由定义为 admin/manage 且无 auth 装饰器（文件级启发式，降级 MEDIUM 避免误报）
    ('admin_route_no_auth', re.compile(
        r'@(?:app|router|blueprint|bp)\.(?:get|post|put|delete|route)\s*\(\s*[\'"][^\'"]*(?:admin|manage|internal)[^\'"]*[\'"]'), 'MEDIUM', '管理类路由（确认已有鉴权保护）'),
]

TEST_ACCOUNT_RULES = [
    ('test_account', re.compile(
        r'(?i)(?:test|demo|guest)\s*(?:_?\s*account|user)?\s*[:=]\s*[\'"][^\'"]{3,40}[\'"]'), 'LOW', '疑似测试账号残留（上线前清理）'),
    ('seed_admin', re.compile(
        r'(?i)(?:create|insert|seed)[^\n]{0,50}(?:admin|root)[^\n]{0,80}(?:password|passwd|pwd)[^\n]{0,30}[\'"][^\'"]{3,}[\'"]'), 'MEDIUM', '种子数据含管理员账号密码（上线前确认已移除）'),
]

DOCKER_RULES = [
    ('docker_privileged', re.compile(r'(?i)privileged\s*:\s*true'), 'CRITICAL', '容器 privileged 特权模式'),
    ('docker_sock_mount', re.compile(r'/var/run/docker\.sock'), 'CRITICAL', '挂载 docker.sock'),
    ('cap_add_sysadmin', re.compile(r'(?i)cap_add[\s\S]{0,40}SYS_ADMIN'), 'HIGH', '容器 SYS_ADMIN 权限'),
    ('network_host', re.compile(r'(?i)network_mode\s*:\s*host'), 'HIGH', '容器 host 网络模式'),
    ('db_port_public', re.compile(r'(?i)(?:3306|5432|6379|27017)\s*:\s*(?:3306|5432|6379|27017)'), 'MEDIUM', '数据库端口直接对外映射'),
]

# Dockerfile 专属（build 配置层面；Secret 已由通用规则覆盖）
DOCKERFILE_RULES = [
    ('dockerfile_user_root', re.compile(r'(?im)^USER\s+root\s*$'), 'MEDIUM', 'Dockerfile 显式以 root 运行'),
    ('dockerfile_add_url', re.compile(r'(?im)^ADD\s+https?://'), 'MEDIUM', 'ADD 远程 URL（不可审计且不可复现，建议 RUN curl + 校验）'),
    ('dockerfile_latest_tag', re.compile(r'(?im)^FROM\s+\S+:latest'), 'LOW', '基础镜像使用 latest 标签，构建不可复现'),
]

PRIVACY_RULES = [
    ('cn_phone', re.compile(r'(?<![\d-])1[3-9]\d{9}(?![\d])'), 'HIGH', '疑似真实手机号'),
    ('cn_id_card', re.compile(r'(?<![\dXx])\d{17}[\dXx](?![\dXx])'), 'HIGH', '疑似身份证号'),
]

# ---------- 误报复核沉淀（documentation example / 人工 FALSE_POSITIVE） ----------

REVIEW_FILE = '.appship/security-review.json'


def doc_context_at(path: Path, text: str, pos: int) -> bool:
    """判断 pos 处是否处于文档示例上下文:
    - Markdown: 代码围栏 ``` 内
    - HTML: <pre>/<code> 块内，或所在行位于表格单元格 <td> / <meta> / <title> 内（教程表格与营销文案）
    文档示例中的 admin/admin、DEBUG=True 属教学内容而非真实配置 → 降级 LOW。
    """
    name = path.name.lower()
    if name.endswith('.md'):
        return text[:pos].count('```') % 2 == 1
    if name.endswith(('.html', '.htm')):
        before = text[:pos]
        if before.count('<pre') > before.count('</pre'):
            return True
        if before.count('<code') > before.count('</code'):
            return True
        line_start = before.rfind('\n') + 1
        line_end = text.find('\n', pos)
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        if any(tag in line for tag in ('<td', '</td>', '<meta', '<title')):
            return True
    return False


def load_dispositions(root: Path) -> list:
    p = root / REVIEW_FILE
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding='utf-8')).get('dispositions', [])
    except (json.JSONDecodeError, OSError):
        return []


def save_disposition(root: Path, file: str, line: int, rule: str, reason: str) -> Path:
    p = root / REVIEW_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {'dispositions': []}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            pass
    from datetime import datetime, timezone
    data['dispositions'].append({
        'file': file, 'line': line, 'rule': rule,
        'disposition': 'FALSE_POSITIVE', 'reason': reason,
        'reviewed': True, 'reviewed_at': datetime.now(timezone.utc).isoformat(),
    })
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return p


def disposition_matches(d: dict, f: dict) -> bool:
    """按 file+rule 匹配（line 为 0 表示整条规则级豁免）。"""
    if d.get('file') != f['file'] or d.get('rule') != f.get('rule'):
        return False
    return d.get('line') in (0, f.get('line'))


def is_placeholder(value: str) -> bool:
    v = value.lower()
    if v in ('password', 'secret', 'token', '123456', '12345678', 'admin'):
        return True
    return any(w in v for w in PLACEHOLDER_WORDS)


def iter_scan_files(root: Path):
    count = 0
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if p.name in LOCK_FILES:
            continue
        rel = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel[:-1]):
            continue
        if p.suffix in SECRET_FILE_EXTS:
            yield p, 'secret_file'
            count += 1
            continue
        if p.suffix in SCAN_EXTS or p.name in SCAN_NAMES or p.name.startswith('.env'):
            yield p, 'text'
            count += 1
        if count >= MAX_FILES:
            return


def read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
        if b'\x00' in data[:4096]:
            return ''
        return data[:MAX_TEXT_BYTES].decode('utf-8', errors='ignore').lstrip('\ufeff')
    except OSError:
        return ''


def scan_file(path: Path, rel: str, findings: list):
    text = read_text(path)
    if not text:
        # 私钥类文件即使读不出内容也提示存在
        if path.suffix in SECRET_FILE_EXTS:
            findings.append(make_finding('secret_file_exists', 'HIGH', rel, 1, '存在私钥/证书文件，禁止进入构建产物'))
        return

    is_test = any(k in path.name.lower() for k in ('test', 'spec', 'mock', 'fixture'))
    raw = []  # (finding, match_pos) — 末尾统一做文档示例降级

    def push(rule_id, sev, desc, pos):
        raw.append((make_finding(rule_id, sev, rel, text.count('\n', 0, pos) + 1, desc), pos))

    # Secret 规则
    for rule_id, pattern, sev, desc in SECRET_RULES:
        for m in re.finditer(pattern, text):
            push(rule_id, sev, desc, m.start())

    # 通用硬编码键值对
    for m in GENERIC_SECRET.finditer(text):
        value = m.group(2)
        if not is_placeholder(value):
            push('hardcoded_secret', 'HIGH', f'硬编码敏感字段 {m.group(1)}（值已隐藏）', m.start())

    # 弱口令
    for m in WEAK_PASSWORDS.finditer(text):
        push('weak_password', 'HIGH', f'弱口令/默认账号: {m.group(0)}', m.start())

    # Debug / CORS / 危险代码 / Docker / 隐私 / 管理路由 / 测试账号
    for rules in (DEBUG_RULES, CORS_RULES, ADMIN_ROUTE_RULES, TEST_ACCOUNT_RULES):
        for rule_id, regex, sev, desc in rules:
            for m in regex.finditer(text):
                push(rule_id, sev, desc, m.start())

    for rule_id, regex, sev, desc in DANGEROUS_CODE_RULES:
        for m in regex.finditer(text):
            push(rule_id, sev, desc, m.start())

    # docker-compose 专属
    if path.name in SCAN_NAMES and 'compose' in path.name.lower():
        for rule_id, regex, sev, desc in DOCKER_RULES:
            for m in regex.finditer(text):
                push(rule_id, sev, desc, m.start())

    # Dockerfile 专属（通用 docker 规则同样适用，如 docker.sock 引用）
    if path.name == 'Dockerfile':
        for rule_id, regex, sev, desc in DOCKER_RULES + DOCKERFILE_RULES:
            for m in regex.finditer(text):
                push(rule_id, sev, desc, m.start())
        if not re.search(r'(?im)^USER\s+\S', text):
            raw.append((make_finding('dockerfile_no_user', 'MEDIUM', rel, 0,
                                     'Dockerfile 无 USER 指令，容器默认以 root 运行'), 0))

    # 隐私数据（测试文件降级 LOW）
    for rule_id, regex, sev, desc in PRIVACY_RULES:
        for m in regex.finditer(text):
            push(rule_id, 'LOW' if is_test else sev, desc, m.start())

    # 文档示例降级：非 CRITICAL 发现位于 md 代码块 / html code/pre/td 教学上下文 → LOW
    for f, pos in raw:
        if f['severity'] != 'CRITICAL' and pos and doc_context_at(path, text, pos):
            f['original_severity'] = f['severity']
            f['severity'] = 'LOW'
            f['context'] = 'documentation_example'
            f['confidence'] = 'low'
            f['desc'] = f"{f['desc']}（文档示例，已降级）"
        findings.append(f)


def make_finding(rule_id, severity, file, line, desc):
    return {'rule': rule_id, 'severity': severity, 'file': file.replace('\\', '/'), 'line': line, 'desc': desc}


def check_env_gitignore(root: Path, findings: list):
    env_exists = (root / '.env').is_file()
    if not env_exists:
        return
    gi = root / '.gitignore'
    gi_text = read_text(gi) if gi.is_file() else ''
    if '.env' not in gi_text:
        findings.append(make_finding('env_not_ignored', 'MEDIUM', '.env', 0, '.env 存在但未加入 .gitignore，有提交泄漏风险'))


def scan(root: Path) -> dict:
    findings = []
    for path, kind in iter_scan_files(root):
        rel = str(path.relative_to(root))
        scan_file(path, rel, findings)
    check_env_gitignore(root, findings)

    # 应用人工复核沉淀（.appship/security-review.json 中的 FALSE_POSITIVE）
    dispositions = load_dispositions(root)
    reviewed = 0
    for f in findings:
        if any(d.get('disposition') == 'FALSE_POSITIVE' and disposition_matches(d, f) for d in dispositions):
            f['disposition'] = 'FALSE_POSITIVE'
            f['original_severity'] = f.get('original_severity') or f['severity']
            f['reviewed'] = True
            reviewed += 1

    active = [f for f in findings if not f.get('reviewed')]
    doc_downgraded = sum(1 for f in active if f.get('context') == 'documentation_example')

    counts = {'CRITICAL': 0, 'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
    for f in active:
        counts[f['severity']] += 1

    if counts['CRITICAL'] > 0:
        status = 'BLOCKED'
    elif counts['HIGH'] > 0:
        status = 'REVIEW_REQUIRED'
    elif reviewed > 0 or doc_downgraded > 0:
        status = 'PASS_WITH_REVIEW'
    else:
        status = 'PASS'

    return {
        'project': root.name,
        'status': status,
        'deploy_allowed': status in ('PASS', 'PASS_WITH_REVIEW'),
        'counts': counts,
        'findings': findings,
        'reviewed': reviewed,
        'doc_downgraded': doc_downgraded,
    }


# ---------- 落盘 ----------

def save_report(root: Path, result: dict) -> Path:
    """落盘 security_report.json（v0.5 P0 输出物）。"""
    out = root / '.appship' / 'security_report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return out


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(description='AppShip Step 2: Security Gate')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--save', action='store_true', help='落盘 .appship/security_report.json')
    parser.add_argument('--mark-fp', metavar='file:line:rule',
                        help='把某条发现标记为 FALSE_POSITIVE 并沉淀到 .appship/security-review.json')
    parser.add_argument('--reason', default='人工复核确认为误报', help='--mark-fp 的复核理由')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    if args.mark_fp:
        parts = args.mark_fp.rsplit(':', 2)
        if len(parts) != 3 or not parts[0]:
            print('错误: --mark-fp 格式为 file:line:rule', file=sys.stderr)
            sys.exit(2)
        file, line, rule = parts[0], int(parts[1]), parts[2]
        p = save_disposition(root, file, line, rule, args.reason)
        print(f'已记录复核结论 → {p}')
        print(f'  {file}:{line} {rule} = FALSE_POSITIVE（{args.reason}）')

    result = scan(root)

    if args.save:
        p = save_report(root, result)
        if not args.json:
            print(f'已写入 {p}')

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"项目: {result['project']}")
        print(f"安全状态: {result['status']}  (Critical:{result['counts']['CRITICAL']} High:{result['counts']['HIGH']} "
              f"Medium:{result['counts']['MEDIUM']} Low:{result['counts']['LOW']})")
        if result.get('reviewed'):
            print(f"  已人工复核误报: {result['reviewed']} 项（不计入计数）")
        if result.get('doc_downgraded'):
            print(f"  文档示例自动降级: {result['doc_downgraded']} 项")
        print()
        order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
        for f in sorted(result['findings'], key=lambda x: order[x['severity']]):
            mark = ' [已复核FP]' if f.get('reviewed') else (' [文档示例]' if f.get('context') == 'documentation_example' else '')
            print(f"  [{f['severity']:8}] {f['file']}:{f['line']}  {f['desc']} ({f['rule']}){mark}")
    return result


if __name__ == '__main__':
    main()
