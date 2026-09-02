#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview_client.py — AppShip v0.4.1 / Preview Client

职责:
  1. sanitized bundle 打包（v0.5 §20）:
     排除 .git / .env / 私钥 / node_modules / 构建产物 / 大文件
  2. 调用 Control Plane API 创建 Preview 并轮询到 RUNNING

配置（优先级: 项目 .appship/client.json > 全局 ~/.appship/client.json）:
    {
      "api_url": "https://cp.appship.top",
      "preview_key": "appship-xxxx"
    }

用法:
    python scripts/preview_client.py /path/to/project --pack     # 仅打包
    python scripts/preview_client.py /path/to/project --request  # 打包+上传+等待就绪
    python scripts/preview_client.py /path/to/project --request --auto-key
                                     # 未配置时启用内置免费体验（2 次临时验证，无需领 Key）
    python scripts/preview_client.py --list                      # 我的 Preview 列表
    python scripts/preview_client.py --destroy <job_id>          # 销毁
"""

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

# v0.5 §20 sanitized bundle 排除规则
EXCLUDE_DIRS = {
    '.git', '.svn', 'node_modules', 'venv', '.venv', 'env', '__pycache__',
    'dist', 'build', 'out', '.next', '.nuxt', '.turbo', '.cache', 'coverage',
    '.pytest_cache', '.idea', '.vscode', 'target', 'vendor', '.appship',
}
EXCLUDE_FILES = {
    '.env', '.env.local', '.env.production', '.env.development.local',
    '.env.production.local', 'id_rsa', 'id_ed25519',
}
EXCLUDE_EXTS = {'.pyc', '.pyo', '.log', '.zip', '.tar', '.gz', '.7z', '.exe', '.dll', '.so'}
EXCLUDE_NAME_PATTERNS = ('.pem', '.key', '.pfx', '.p12', '.keystore')
MAX_FILE_BYTES = 5 * 1024 * 1024      # 单文件 5MB
MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 总包 200MB


# ---------- 配置 ----------

def load_client_config(project_root: Path) -> dict:
    """优先级: 项目 .appship/client.json > skill目录/.appship/client.json > ~/.appship/client.json"""
    candidates = [
        project_root / '.appship' / 'client.json',
        Path(__file__).parent.parent / '.appship' / 'client.json',
        Path.home() / '.appship' / 'client.json',
    ]
    for c in candidates:
        if c.is_file():
            try:
                # utf-8-sig：容忍 Windows 记事本等工具写入的 BOM 头
                return json.loads(c.read_text(encoding='utf-8-sig'))
            except (json.JSONDecodeError, OSError):
                continue
    return {}


# ---------- 打包 ----------

def should_include(path: Path, rel: Path) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return False
    name = path.name
    if name in EXCLUDE_FILES or (name.startswith('.env') and name != '.env.example'):
        return False
    if any(pat in name.lower() for pat in EXCLUDE_NAME_PATTERNS):
        return False
    if path.suffix.lower() in EXCLUDE_EXTS:
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return True


def make_bundle(root: Path, extra_files: dict | None = None) -> dict:
    out_dir = root / '.appship'
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = out_dir / 'bundle.zip'

    included, excluded_secret = [], []
    total = 0
    with zipfile.ZipFile(bundle_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob('*'):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if not should_include(p, rel):
                if rel.name.startswith('.env') and rel.name != '.env.example':
                    excluded_secret.append(str(rel))
                continue
            total += p.stat().st_size
            if total > MAX_TOTAL_BYTES:
                return {'ok': False, 'error': f'打包总大小超过 {MAX_TOTAL_BYTES // 1024 // 1024}MB 上限'}
            zf.write(p, rel)
            included.append(str(rel))
        # 追加生成文件（如容器项目缺 Dockerfile 时由 skill 生成，不写入用户项目）
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
            included.append(f'{name} (generated)')

    return {
        'ok': True,
        'bundle': str(bundle_path),
        'files': len(included),
        'size_mb': round(total / 1024 / 1024, 2),
        'secrets_excluded': excluded_secret[:10],
    }


# ---------- API 客户端（纯标准库 multipart） ----------

class ApiError(RuntimeError):
    pass


def api_request(cfg: dict, method: str, path: str, body: dict | None = None,
                multipart: tuple[str, bytes, str] | None = None) -> dict:
    url = cfg['api_url'].rstrip('/') + path
    # 自标识 UA：Python-urllib 默认 UA 会被 Cloudflare 等反代按已知爬虫拦截（HTTP 403 code 1010）
    headers = {'User-Agent': 'AppShip-Preview-Client/0.4.1'}
    # 领 Key 接口（/v1/key/request）无鉴权；其余接口带 preview_key
    if cfg.get('preview_key'):
        headers['Authorization'] = f"Bearer {cfg['preview_key']}"

    if multipart:
        field, data, filename = multipart
        boundary = f'----appship{uuid.uuid4().hex}'
        parts = []
        # 表单字段
        for k, v in (body or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        # 文件字段
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="bundle"; filename="{filename}"\r\n'
            f'Content-Type: application/zip\r\n\r\n'.encode() + data + b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        payload = b''.join(parts)
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    else:
        payload = json.dumps(body).encode() if body is not None else None
        if payload:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=payload, method=method, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()
        except Exception:
            detail = ''
        if e.code == 401:
            # key 过期：服务器 detail 带 expired 标记 → 指引官网重新领取
            if 'temp-key expired' in detail:
                # 体验额度过期：用户不感知 Key，话术只讲「体验额度」
                raise ApiError(
                    '本次免费体验额度已到期（体验额度激活后 30 天内有效，未用完的次数随之失效）。\n'
                    '本地检查和技术报告仍可继续免费使用。\n'
                    '→ 领取免费 Preview Key：https://iai66.com/appship/key\n'
                    '  （30 天有效 · 5 次临时验证 · 无需注册，领取后把 Key 交给 AppShip 即可继续预览）\n'
                    '→ 准备正式上线：https://iai66.com') from e
            if 'expired' in detail:
                raise ApiError(
                    '预览 key 已过期。\n'
                    '本地检查和技术报告仍可继续免费使用。\n'
                    '→ 重新领取免费 Key：https://iai66.com/appship/key\n'
                    '→ 准备正式上线：https://iai66.com') from e
            raise ApiError(
                '预览 key 无效。请到 https://iai66.com/appship/key 免费领取一个 Key，'
                '然后更新 .appship/client.json 里的 preview_key') from e
        # FastAPI HTTPException 的 detail 是 JSON（如 429 额度话术），解析出人话部分
        try:
            detail = json.loads(detail).get('detail', detail)
        except (ValueError, AttributeError):
            pass
        raise ApiError(f'HTTP {e.code}: {detail}') from e


# ---------- 主流程 ----------

DEFAULT_API_URL = 'https://cp.appship.top'


def ensure_temp_key(root: Path, cfg: dict, as_json: bool = False) -> dict:
    """--auto-key：启用 AppShip 内置的免费体验额度（2 次临时验证，无需注册、无需领 Key）。

    实现上是向服务端领一个短期 temp key 写入项目配置，但用户层口径只叫「体验额度」。
    额度用完/到期后话术引导去官网领 30 天个人 Preview Key。
    """
    api_url = cfg.get('api_url') or DEFAULT_API_URL
    if not as_json:
        print('正在启用 AppShip 内置的免费体验额度...')
    resp = api_request({'api_url': api_url}, 'POST', '/v1/key/request?kind=temp', body={})
    key = resp.get('key')
    if not key:
        raise ApiError(f'体验额度启用失败: {resp}')
    new_cfg = {'api_url': api_url, 'preview_key': key}
    target = root / '.appship' / 'client.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not as_json:
        print(f'✅ 免费体验额度已启用（{target}）')
        print('   新用户 2 次免费临时验证，无需注册')
        print('   每次生成的临时验证环境有效 24 小时，到期自动销毁')
        print('   需要更多临时验证 → 领取个人 Preview Key：https://iai66.com/appship/key')
        print('   （30 天有效 · 5 次临时验证 · 无需注册，领取后把 Key 交给 AppShip 即可继续预览）')
    return new_cfg


def cmd_pack(root: Path, as_json: bool) -> dict:
    result = make_bundle(root)
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    elif result.get('ok'):
        print(f"打包完成: {result['bundle']}")
        print(f"  文件数: {result['files']}  大小: {result['size_mb']}MB")
        if result.get('secrets_excluded'):
            print(f"  已排除敏感文件: {', '.join(result['secrets_excluded'])}")
    else:
        print(f"打包失败: {result.get('error')}", file=sys.stderr)
        sys.exit(1)
    return result


def cmd_request(root: Path, as_json: bool, auto_key: bool = False, wait_timeout: int = 600) -> dict:
    sys.path.insert(0, str(Path(__file__).parent))
    from detect_stack import detect
    from security_scan import scan as security_scan
    from deployment_decision import decide

    # 预检: BLOCKED 直接拒绝
    sec = security_scan(root)
    if sec['counts']['CRITICAL'] > 0:
        msg = f"存在 {sec['counts']['CRITICAL']} 个 CRITICAL 安全问题，禁止上传 Preview。请先修复（运行 ship.py 查看详情）。"
        print(msg, file=sys.stderr) if not as_json else None
        if as_json:
            print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False))
        sys.exit(1)

    stack = detect(root)
    dec = decide(root)
    ver = dec['verification']

    # ---- 验证形态 gate：MVP 仅支持 service + (http|tcp)，其余类型待 P6 ----
    if not ver['preview_ready']:
        msg = (f"该项目的验证形态暂不支持云端 Preview（当前支持：service + http/tcp）。\n"
               f"检测到: execution_mode={ver['execution_mode']}, transport={ver['transport']}, "
               f"probe={ver['probe']}\n{ver['note']}\n"
               f"run_once / stdio 沙箱验证将在 P6 支持。")
        print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False)) if as_json else \
            print(msg, file=sys.stderr)
        sys.exit(3)

    # ---- 容器类项目：缺 Dockerfile 时由 skill 生成（dry-run 拿内容，不写用户项目） ----
    extra_files = {}
    is_dynamic = dec['type'] in ('CONTAINER', 'COMPOSE_OR_DEDICATED', 'SERVERLESS')
    if is_dynamic and not stack['existing_docker']['dockerfile']:
        from generate_docker import generate as gen_docker
        gen = gen_docker(root, write=False)
        if gen.get('files', {}).get('Dockerfile'):
            extra_files = {k: v for k, v in gen['files'].items() if k in ('Dockerfile', '.dockerignore')}
            if not as_json:
                print(f"项目无 Dockerfile，已自动生成并打包（{', '.join(extra_files)}）")

    # 容器端口：detect 识别的端口优先，否则按 runtime 默认
    container_port = stack.get('ports') or []
    if not container_port:
        container_port = [3000 if stack.get('runtime') == 'node' else 8000]
    container_port = int(container_port[0])

    # stack 摘要（服务端生成 Dockerfile 兜底用）
    fw = stack.get('frameworks') or []
    stack_summary = f"{stack.get('runtime', 'unknown')}:{fw[0]}" if fw else stack.get('runtime', 'unknown')

    cfg = load_client_config(root)
    if not cfg.get('api_url') or not cfg.get('preview_key'):
        if auto_key:
            try:
                cfg = ensure_temp_key(root, cfg, as_json)
            except ApiError as e:
                msg = f'免费体验额度启用失败：{e}'
                print(msg, file=sys.stderr) if not as_json else None
                if as_json:
                    print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False))
                sys.exit(2)
        else:
            msg = ('未配置。两种方式：\n'
                   '① 运行时加 --auto-key：直接使用内置的 2 次免费临时验证（无需注册、无需领 Key）\n'
                   '② 领取个人 Preview Key：https://iai66.com/appship/key'
                   '（30 天有效 · 5 次临时验证 · 无需注册），\n'
                   '   然后创建 client.json（项目 .appship/ 目录或 ~/.appship/）:\n'
                   '{\n  "api_url": "https://cp.appship.top",\n  "preview_key": "你领取的key"\n}')
            print(msg, file=sys.stderr) if not as_json else None
            if as_json:
                print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False))
            sys.exit(2)

    bundle = make_bundle(root, extra_files)
    if not bundle.get('ok'):
        print(f"打包失败: {bundle.get('error')}", file=sys.stderr)
        sys.exit(1)

    data = Path(bundle['bundle']).read_bytes()
    resp = api_request(cfg, 'POST', '/v1/preview/request',
                       body={'project': root.name, 'deploy_mode': dec['type'],
                             'stack': stack_summary, 'container_port': container_port,
                             'execution_mode': ver['execution_mode'],
                             'transport': ver['transport'], 'probe': ver['probe'],
                             'deliverable_kinds': ','.join(ver['deliverable_kinds'])},
                       multipart=('bundle', data, 'bundle.zip'))

    job_id = resp['job_id']
    ttl_h = resp.get('expires_at_hours')
    out = {'ok': True, 'job_id': job_id, 'url': resp.get('url'),
           'deliverables': resp.get('deliverables', []), 'status': resp['status']}

    # 轮询到 RUNNING / FAILED
    start = time.time()
    while time.time() - start < wait_timeout:
        st = api_request(cfg, 'GET', f'/v1/preview/{job_id}')
        out['status'] = st['status']
        if st['status'] in ('RUNNING', 'FAILED', 'DESTROYED'):
            out['url'] = st.get('url')
            out['deliverables'] = st.get('deliverables', out['deliverables'])
            if st['status'] == 'FAILED':
                out['error'] = st['error']
            break
        time.sleep(2)

    # 预览地址实测：HTTPS 优先，不通回退 HTTP（DNS/边缘证书未就绪场景）
    if out['status'] == 'RUNNING':
        url, probe_st = resolve_preview_url(out.get('url'))
        out['url'], out['url_probe'] = url, probe_st
        for d in out.get('deliverables') or []:
            if d.get('kind') in ('web_url', 'api_url') and d.get('url'):
                d['url'], _ = resolve_preview_url(d['url'])

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif out['status'] == 'RUNNING':
        print_user_report(root, dec, out, ttl_h)
    else:
        print(f"状态: {out['status']}")
        if out.get('error'):
            print(f"失败: {out['error']}")

    # 回写 report.md / report.json（注入 Preview 运行信息，若有本地报告）
    try:
        update_local_report(root, dec, out, ttl_h)
    except Exception:
        pass  # 报告更新失败不影响预览结果
    return out


# ---------- 预览地址探测（HTTPS 优先，不通回退 HTTP） ----------

def probe_url(url: str, timeout: int = 10) -> bool:
    """轻量探测：地址当前是否可访问（拿到任何 HTTP 响应即算通——DNS/TLS/服务可达）。"""
    try:
        req = urllib.request.Request(url, method='GET',
                                     headers={'User-Agent': 'AppShip-Preview-Client/0.4.1'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError:
        return True  # 4xx 也是有效响应，说明地址可达
    except Exception:
        return False


def resolve_preview_url(url: str):
    """HTTPS 优先实测；不通回退 HTTP（DNS/边缘证书未就绪时不至于没地址用）。

    返回 (url, status)：status ∈ ok / fallback / pending（都不通=解析生效中）。
    """
    if not url:
        return url, 'unknown'
    if probe_url(url):
        return url, 'ok'
    if url.startswith('https://'):
        alt = 'http://' + url[len('https://'):]
        if probe_url(alt):
            return alt, 'fallback'
    return url, 'pending'


def print_user_report(root: Path, dec: dict, out: dict, ttl_h):
    """Preview 成功后的最终结果摘要（两层之二；完整技术报告在 report.md）。"""
    # 合并本地检测报告的 headline（若刚跑过 ship.py）
    jr = None
    rj_path = root / '.appship' / 'report.json'
    if rj_path.is_file():
        try:
            jr = json.loads(rj_path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            jr = None
    head = (jr or {}).get('headline') or {}
    plan = (jr or {}).get('action_plan') or {}
    W = 60

    print()
    print('=' * W)
    print('AppShip · AI 启航上线助手')
    print('=' * W)
    print()
    # 按交付物类型选标题（Web/API/CLI-Worker 三套文案）
    kinds = [d.get('kind') for d in (out.get('deliverables') or [])]
    if 'web_url' in kinds:
        print('🎉 太棒了，你的网站已经成功跑到公网了')
    elif 'api_url' in kinds:
        print('🎉 API 已成功运行，可以开始调用测试')
    else:
        print('🎉 程序已在隔离环境成功执行')
    print()
    for d in out.get('deliverables') or []:
        if d.get('kind') in ('web_url', 'api_url') and d.get('url'):
            print(f"  {d['url']}   <- {'预览地址' if d['kind'] == 'web_url' else 'API 地址'}")
        elif d.get('summary'):
            print(f"  {d['summary']}")
    print(f"  有效期：{ttl_h or '?'} 小时（到期自动销毁）｜本次临时验证免费")
    print()
    risk = head.get('risk')
    if risk is None:
        print('🟢 严重安全风险：运行 ship.py 获取完整检测')
    else:
        print(f"🟢 严重安全风险：{risk} 项" if risk == 0 else f"🟡 严重安全风险：{risk} 项（需确认）")
    probe = out.get('url_probe')
    if probe == 'ok':
        print('✅ 临时验证：正常（刚实测访问，页面正常打开）')
    elif probe == 'fallback':
        print('✅ 临时验证：正常（HTTPS 尚未就绪，已先给 HTTP 地址）')
    elif probe == 'pending':
        print('⏳ 临时验证：已部署成功，地址解析生效中（几分钟内自动可用）')
    else:
        print('✅ 临时验证：正常')
    todo = head.get('todo')
    if todo is None:
        print('🚀 距离正式上线：运行 ship.py 获取上线清单')
    else:
        print(f'🚀 按当前用途，距离正式上线还差 {todo} 项')
    needed = plan.get('launch_needed') or []
    if needed:
        print()
        print(f"还差：{' / '.join(needed)}")
    icp_note = plan.get('icp_note')
    if icp_note:
        print()
        print(icp_note)
    print()
    print('→ 查看完整技术报告（.appship/report.md）')
    print('→ 我准备正式上线')
    print()
    print('AI 帮你把产品做出来，')
    print('宇视星（iai66.com）负责让它真正上线并持续稳定运行。')
    print('=' * W)


def update_local_report(root: Path, dec: dict, out: dict, ttl_h):
    """把 Preview 运行信息注入本地 report.md / report.json（第 6 节）。"""
    rj_path = root / '.appship' / 'report.json'
    if not rj_path.is_file():
        return
    sys.path.insert(0, str(Path(__file__).parent))
    from report import save_reports
    report = json.loads(rj_path.read_text(encoding='utf-8'))
    preview = {'url': out.get('url'), 'job_id': out.get('job_id'),
               'status': out.get('status'), 'ttl_hours': ttl_h,
               'deploy_mode': dec.get('type'),
               'host_port': (out.get('verification') or {}).get('host_port'),
               'container_port': (out.get('verification') or {}).get('container_port'),
               'expires_at': out.get('expires_at')}
    save_reports(report, root / '.appship', preview=preview)


def cmd_list(root: Path, as_json: bool) -> dict:
    cfg = load_client_config(root)
    resp = api_request(cfg, 'GET', '/v1/preview')
    if as_json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        for j in resp['jobs']:
            urls = [d.get('url') for d in (j.get('deliverables') or [])
                    if d.get('kind') in ('web_url', 'api_url') and d.get('url')]
            shown = urls[0] if urls else (j.get('url') or '-')
            print(f"  {j['id']}  {j['status']:8}  {shown}  ({j['project_name']})")
    return resp


def cmd_destroy(root: Path, job_id: str, as_json: bool) -> dict:
    cfg = load_client_config(root)
    resp = api_request(cfg, 'DELETE', f'/v1/preview/{job_id}')
    if as_json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    else:
        print('✅ 销毁请求已提交')
        print(f"  Job ID: {job_id} → {resp['status']}")
        print('  资源清理约需数秒，随后预览地址返回 404')
    return resp


def main():
    try:
        _main()
    except ApiError as e:
        print(f'❌ {e}', file=sys.stderr)
        sys.exit(1)


def _main():
    parser = argparse.ArgumentParser(description='AppShip: Preview Client')
    parser.add_argument('project', nargs='?', help='项目路径')
    parser.add_argument('--pack', action='store_true', help='仅打包 sanitized bundle')
    parser.add_argument('--request', action='store_true', help='打包+上传+等待就绪')
    parser.add_argument('--list', action='store_true', help='列出我的 Preview')
    parser.add_argument('--destroy', metavar='JOB_ID', help='销毁指定 Preview')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--auto-key', action='store_true',
                        help='未配置时启用内置免费体验额度（2 次临时验证，无需注册、无需领 Key）')
    args = parser.parse_args()

    if not any((args.pack, args.request, args.list, args.destroy)):
        print('请指定 --pack / --request / --list / --destroy <job_id>', file=sys.stderr)
        sys.exit(2)

    if args.list or args.destroy:
        root = Path(args.project or '.').resolve()
        if args.list:
            cmd_list(root, args.json)
        else:
            cmd_destroy(root, args.destroy, args.json)
        return

    if not args.project:
        print('缺少项目路径', file=sys.stderr)
        sys.exit(2)
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)
    sys.path.insert(0, str(Path(__file__).parent))
    from detect_stack import is_project_dir
    if not is_project_dir(root):
        print(f'错误: {root} 里没有找到项目文件（目录为空或只有生成物）。'
              f'请确认项目路径，或先把代码放进来。', file=sys.stderr)
        sys.exit(2)

    if args.pack:
        cmd_pack(root, args.json)
    elif args.request:
        cmd_request(root, args.json, auto_key=args.auto_key)


if __name__ == '__main__':
    main()
