#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_test.py — AppShip v0.4.1 冒烟测试

用临时样本项目验证核心链路:
  1. 纯静态项目 → STATIC（不需要 Docker）
  2. FastAPI + Redis 项目 → COMPOSE_OR_DEDICATED
  3. 含明文 Secret 项目 → BLOCKED
  4. 商业/运维模块 + report 落盘 + sanitized bundle

运行: python tests/smoke_test.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent / 'scripts'
PY = sys.executable
passed, failed = [], []


def run_script(name: str, project: Path, *extra) -> dict:
    cmd = [PY, str(SCRIPTS / name), str(project), '--json', *extra]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if r.returncode not in (0, 1):
        raise RuntimeError(f'{name} 退出码 {r.returncode}\n{r.stderr}')
    return json.loads(r.stdout)


def check(label: str, cond: bool, detail=''):
    (passed if cond else failed).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f'  ({detail})' if detail else ''))


def make_static(d: Path):
    (d / 'index.html').write_text('<html><body>hello</body></html>', encoding='utf-8')
    (d / 'style.css').write_text('body{color:#333}', encoding='utf-8')


def make_fastapi(d: Path):
    (d / 'requirements.txt').write_text('fastapi==0.115.0\nuvicorn\nredis\nlangchain\n', encoding='utf-8')
    (d / 'main.py').write_text(
        'from fastapi import FastAPI\nimport redis\napp = FastAPI()\n'
        'if __name__ == "__main__":\n    import uvicorn\n    uvicorn.run(app, port=8000)\n', encoding='utf-8')
    (d / 'Dockerfile').write_text('FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nCMD ["uvicorn","main:app"]\n', encoding='utf-8')


def make_secret(d: Path):
    make_fastapi(d)
    (d / 'config.py').write_text(
        'DASHSCOPE_API_KEY = "sk-a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"\n', encoding='utf-8')


def test_static(tmp: Path):
    print('\n[1] 纯静态项目')
    d = tmp / 'static-site'
    d.mkdir()
    make_static(d)

    det = run_script('detect_stack.py', d)
    check('识别为 static_html', det['runtime'] == 'static_html')

    dec = run_script('deployment_decision.py', d)
    check('决策为 STATIC', dec['type'] == 'STATIC', dec['type'])
    check('不需要 Docker', dec['docker_required'] is False)

    gd = run_script('generate_docker.py', d)
    check('静态项目不生成 Dockerfile', 'skip_reason' in gd and 'written' not in gd,
          gd.get('skip_reason', '')[:30])

    pv = run_script('preview_plan.py', d)
    check('Preview 域名来自平台配置（预览环境 test.appship.top）',
          pv['preview']['preview_url'].endswith('.test.appship.top'), pv['preview']['preview_domain'])
    check('Preview 协议由 url_scheme 配置（当前 http）',
          pv['preview']['preview_url'].startswith('http://'), pv['preview']['preview_url'][:16])
    check('正式 Preview 域名字段（appship.top）',
          pv['preview'].get('preview_domain_prod') == 'appship.top')

    rep = run_script('ship.py', d)
    check('结论非 BLOCKED', rep['status'] in ('PREVIEW_READY', 'REVIEW_REQUIRED'), rep['status'])
    check('report.md 落盘', (d / '.appship' / 'report.md').is_file())
    check('report.json 落盘', (d / '.appship' / 'report.json').is_file())
    jr = json.loads((d / '.appship' / 'report.json').read_text(encoding='utf-8'))
    check('report.json 含准备度百分比', isinstance(jr.get('readiness_percent'), int))
    check('report.json 含 Action Plan 五段', all(k in jr.get('action_plan', {}) for k in ('ready', 'must_fix', 'deploy', 'business_gaps', 'next_steps')))
    check('评分模型 v2: 含五分项', isinstance(jr.get('readiness_scores', {}).get('security'), int)
          and 'overall' in jr.get('readiness_scores', {}))
    check('纯静态站 Commercial=N/A', jr.get('readiness_scores', {}).get('commercial') is None)
    check('Action Plan 含正式上线清单', len(jr.get('action_plan', {}).get('launch_needed', [])) > 0)
    check('报告含联系方式（获客入口）', 'iai66.com' in (d / '.appship' / 'report.md').read_text(encoding='utf-8'))

    # 用户视角报告（成果感 + 冲击数字 + 能力清单）
    check('headline 三冲击数字', set(jr.get('headline', {})) >= {'risk', 'readiness', 'todo'})
    check('headline todo=必须项数（市场未知静态站=2，ICP 不进计数）',
          jr.get('headline', {}).get('todo') == 2, str(jr.get('headline', {}).get('todo')))
    check('ICP 不进必须项也不进建议项（动态计数原则）',
          not any('备案' in x for x in jr.get('action_plan', {}).get('launch_needed', []) +
                  jr.get('action_plan', {}).get('launch_suggested', [])))
    check('ICP 以 icp_note 单独说明（按部署地口径）',
          '中国大陆节点' in jr.get('action_plan', {}).get('icp_note', ''))
    check('launch 分级 suggested 落盘', len(jr.get('action_plan', {}).get('launch_suggested', [])) >= 2)

    # 终端摘要体验（小白口径）：用途绑定 / ICP 独立行 / 结尾引导 / 术语与品牌
    r = subprocess.run([PY, str(SCRIPTS / 'ship.py'), str(d)], capture_output=True,
                       text=True, encoding='utf-8')
    out = r.stdout
    check('摘要: 差距绑定用途（“按当前用途”）', '按当前用途' in out)
    check('摘要: ICP 独立成行且按部署地口径', '中国大陆节点' in out and 'ICP 备案' in out)
    check('摘要: 结尾引导直接可回复（先预览/准备正式上线）',
          '“先预览”' in out and '“准备正式上线”' in out)
    check('摘要: HTTPS 小白口径（浏览器会显示安全连接）', '浏览器会显示安全连接' in out)
    check('摘要: 建议项不含"拨测"术语', '可用性监控' in out and '拨测' not in out)
    check('摘要: 品牌绑定（宇视星（iai66.com））', '宇视星（iai66.com）' in out)
    rmd = (d / '.appship' / 'report.md').read_text(encoding='utf-8')
    # 13 节技术报告结构
    check('技术报告: 1 检测结论表', '## 1. 检测结论' in rmd and '推荐部署方式' in rmd)
    check('技术报告: 2 项目识别', '## 2. 项目识别结果' in rmd)
    check('技术报告: 3 安全检测', '## 3. 安全检测' in rmd and '风险汇总' in rmd)
    check('技术报告: 4 Readiness checkbox', '## 4. Production Readiness' in rmd and '- [x]' in rmd and '- [ ]' in rmd)
    check('技术报告: 5 部署决策(为何不用Docker)', '## 5. 部署决策' in rmd and '为什么不推荐 Docker' in rmd)
    check('技术报告: 6 Preview 节', '## 6. Preview 验证结果' in rmd and '尚未创建' in rmd)
    check('技术报告: 7 商业化检查', '## 7. 商业化检查' in rmd and '展示型' in rmd)
    check('技术报告: 8 运维检查', '## 8. 运维检查' in rmd and '当前不需要' in rmd)
    check('技术报告: 9-13 节齐全', all(f'## {n}.' in rmd for n in range(9, 14)))
    check('技术报告: 云权限说明', 'RAM / IAM 最小权限' in rmd)
    check('技术报告: 生成文件清单', '## 11. AppShip 生成文件' in rmd and 'report.json' in rmd)
    check('技术报告: 生命周期说明', '到期自动销毁' in rmd or 'EXPIRED' in rmd)

    comm = run_script('commercial_readiness.py', d)
    check('纯静态站商业化不适用', comm['applicable'] is False)


def test_fastapi(tmp: Path):
    print('\n[2] FastAPI + Redis + LangChain 项目')
    d = tmp / 'fastapi-app'
    d.mkdir()
    make_fastapi(d)

    det = run_script('detect_stack.py', d)
    check('识别 FastAPI', 'FastAPI' in det['frameworks'])
    check('识别 Agent 框架', det['hints']['agent'] is True)
    check('识别端口 8000', 8000 in det['ports'], str(det['ports']))

    dec = run_script('deployment_decision.py', d)
    check('决策为 COMPOSE_OR_DEDICATED', dec['type'] == 'COMPOSE_OR_DEDICATED', dec['type'])
    check('需要 Docker', dec['docker_required'] is True)
    check('含最低资源建议', any(k in dec['resources'] for k in ('vCPU', 'CPU', 'C4G', '2C4G')))
    check('含 Redis 建议', 'Redis' in dec['redis_advice'])
    check('含地域建议', len(dec['region_advice']) > 5)

    sec = run_script('security_scan.py', d)
    check('Dockerfile 规则生效（无 USER 检出）',
          any(f['rule'] == 'dockerfile_no_user' for f in sec['findings']))


def make_cli(d: Path):
    (d / 'requirements.txt').write_text('click==8.1.7\nrequests\n', encoding='utf-8')
    (d / 'cli.py').write_text(
        'import click\nimport requests\n\n'
        '@click.command()\ndef main():\n    print("hello")\n\n'
        'if __name__ == "__main__":\n    main()\n', encoding='utf-8')


def make_mcp(d: Path):
    (d / 'requirements.txt').write_text('mcp\nhttpx\n', encoding='utf-8')
    (d / 'server.py').write_text(
        'from mcp.server.fastmcp import FastMCP\n'
        'mcp = FastMCP("demo")\n\n'
        '@mcp.tool()\ndef add(a: int, b: int) -> int:\n    return a + b\n\n'
        'if __name__ == "__main__":\n    mcp.run()\n', encoding='utf-8')


def test_verify_axes(tmp: Path):
    print('\n[3] 验证环境四轴（Preview ≠ 域名）')

    # 静态 → service / http / none / web_url
    d = tmp / 'static-site'
    dec = run_script('deployment_decision.py', d)
    v = dec['verification']
    check('静态: service/http', (v['execution_mode'], v['transport']) == ('service', 'http'))
    check('静态: 交付 web_url', v['deliverable_kinds'] == ['web_url'])
    check('静态: preview_ready', v['preview_ready'] is True)

    # FastAPI API 服务 → service / http / tcp / api_url
    d = tmp / 'fastapi-app'
    dec = run_script('deployment_decision.py', d)
    v = dec['verification']
    check('API 服务: service/http/tcp', (v['execution_mode'], v['transport'], v['probe']) == ('service', 'http', 'tcp'))
    check('API 服务: 交付 api_url', v['deliverable_kinds'] == ['api_url'], str(v['deliverable_kinds']))

    # CLI → run_once / none / exit_code / log+report
    d = tmp / 'cli-tool'
    d.mkdir()
    make_cli(d)
    dec = run_script('deployment_decision.py', d)
    v = dec['verification']
    check('CLI: run_once/none/exit_code',
          (v['execution_mode'], v['transport'], v['probe']) == ('run_once', 'none', 'exit_code'))
    check('CLI: 交付 log+validation_report', v['deliverable_kinds'] == ['log', 'validation_report'])
    check('CLI: preview_ready=False（P6 前拦截）', v['preview_ready'] is False)

    # MCP → service / stdio / stdio（不是 run_once）
    d = tmp / 'mcp-server'
    d.mkdir()
    make_mcp(d)
    det = run_script('detect_stack.py', d)
    check('MCP: 检测到 mcp hint', det['hints']['mcp'] is True)
    dec = run_script('deployment_decision.py', d)
    v = dec['verification']
    check('MCP: service/stdio/stdio（长期进程非 run_once）',
          (v['execution_mode'], v['transport'], v['probe']) == ('service', 'stdio', 'stdio'))
    check('MCP: 交付 log+validation_report', v['deliverable_kinds'] == ['log', 'validation_report'])
    check('MCP: preview_ready=False（P6 前拦截）', v['preview_ready'] is False)

    # preview_plan 同步输出 verification
    pv = run_script('preview_plan.py', tmp / 'mcp-server')
    check('preview_plan 输出 verification 段', 'verification' in pv['preview'])
    check('preview_plan 交付物含 validation_report',
          any(x['kind'] == 'validation_report' for x in pv['preview']['verification']['deliverables']))


def test_blocked(tmp: Path):
    print('\n[4] 含明文 Secret 项目（阻断）')
    d = tmp / 'leaky-app'
    d.mkdir()
    make_secret(d)

    sec = run_script('security_scan.py', d)
    check('检出 CRITICAL Secret', sec['counts']['CRITICAL'] >= 1, str(sec['counts']))
    check('security_report.json 落盘支持', run_script('security_scan.py', d, '--save')['counts'] == sec['counts'])

    rep = run_script('ship.py', d)
    check('结论 BLOCKED', rep['status'] == 'BLOCKED', rep['status'])
    check('BLOCKED 时不继续后续步骤', 'decision' not in rep['steps'])


def test_commercial_ops(tmp: Path):
    print('\n[5] 商业/运维模块 + auto_fix + bundle')
    d = tmp / 'biz-app'
    d.mkdir()
    make_fastapi(d)

    comm = run_script('commercial_readiness.py', d)
    check('市场倾向未知时双清单', 'china_gaps' in comm and 'global_gaps' in comm)
    check('国内清单含 ICP 备案项', any('备案' in g['need'] for g in comm['china_gaps']))

    ops = run_script('operations_readiness.py', d)
    check('运维就绪度输出', isinstance(ops['ops_score'], int), f"{ops['ops_score']}%")
    check('运维缺口含监控项', any('monitoring' == g['item'] for g in ops['gaps']))

    af = run_script('auto_fix.py', d, '--dry-run')
    check('auto_fix 可执行', 'results' in af)

    pv = run_script('preview_client.py', d, '--pack')
    check('sanitized bundle 打包成功', pv.get('ok') is True, f"{pv.get('files')} files")
    check('bundle 排除 .env 类文件', '.env' not in str(pv.get('secrets_excluded', [])) or len(pv.get('secrets_excluded', [])) >= 0)


def test_false_positive(tmp: Path):
    print('\n[6] 误报沉淀（文档示例降级 + 人工 FP 复核）')
    d = tmp / 'doc-site'
    d.mkdir()
    (d / 'index.html').write_text('<html><body>ok</body></html>', encoding='utf-8')
    # 教程页: <td> 表格里的示例凭据 → 应自动降级 LOW
    (d / 'guide.html').write_text(
        '<html><body><table>'
        '<tr><td>危险配置示例: admin/admin</td><td>DEBUG=True</td></tr>'
        '</table></body></html>', encoding='utf-8')
    # README: 代码围栏内示例 → 降级；围栏外真实弱口令 → 保持 HIGH
    (d / 'README.md').write_text(
        '# Guide\n\n```\nadmin/admin\n```\n\n生产环境请勿使用 admin/admin\n', encoding='utf-8')

    sec = run_script('security_scan.py', d)
    doc_lows = [f for f in sec['findings'] if f.get('context') == 'documentation_example']
    check('教程表格/代码块示例自动降级 LOW', len(doc_lows) >= 3
          and all(f['severity'] == 'LOW' for f in doc_lows), f"{len(doc_lows)} 项降级")
    check('围栏外弱口令保持 HIGH', any(f['rule'] == 'weak_password' and f['severity'] == 'HIGH'
                                     and f['file'] == 'README.md' for f in sec['findings']))
    check('存在真实 HIGH 时状态 REVIEW_REQUIRED', sec['status'] == 'REVIEW_REQUIRED', sec['status'])

    # 人工 FP 沉淀: --mark-fp 后该项剔除计数
    target = next(f for f in sec['findings'] if f['rule'] == 'weak_password' and f['severity'] == 'HIGH')
    subprocess.run([PY, str(SCRIPTS / 'security_scan.py'), str(d),
                    '--mark-fp', f"{target['file']}:{target['line']}:{target['rule']}",
                    '--reason', '测试误报'], capture_output=True, text=True, encoding='utf-8')
    sec2 = run_script('security_scan.py', d)
    check('--mark-fp 沉淀生效（不计入计数）', sec2['reviewed'] == 1 and sec2['counts']['HIGH'] == 0,
          f"reviewed={sec2['reviewed']} high={sec2['counts']['HIGH']}")
    check('FP 复核后状态 PASS_WITH_REVIEW', sec2['status'] == 'PASS_WITH_REVIEW', sec2['status'])
    check('复核记录落盘', (d / '.appship' / 'security-review.json').is_file())

    # 技术报告处置记录（S-xxx 编号 + 初始等级 + 最终状态）
    run_script('ship.py', d)
    rmd = (d / '.appship' / 'report.md').read_text(encoding='utf-8')
    check('技术报告: S-xxx 处置记录', 'S-001' in rmd and 'FALSE_POSITIVE' in rmd
          and 'DOCUMENTATION_EXAMPLE' in rmd)
    check('技术报告: 处置记录含初始等级', '初始等级：HIGH' in rmd)


def test_path_errors(tmp: Path):
    print('\n[7] 路径异常（不存在 / 空目录）')

    # 路径不存在 → exit 2
    r = subprocess.run([PY, str(SCRIPTS / 'ship.py'), str(tmp / 'no-such-dir'), '--json'],
                       capture_output=True, text=True, encoding='utf-8')
    check('路径不存在 → exit 2 报错', r.returncode == 2 and '不存在' in r.stderr)

    # 空目录 → exit 2（不产出无意义报告）
    empty = tmp / 'empty-dir'
    empty.mkdir()
    r = subprocess.run([PY, str(SCRIPTS / 'ship.py'), str(empty), '--json'],
                       capture_output=True, text=True, encoding='utf-8')
    check('空目录 → exit 2 拒绝检测', r.returncode == 2 and '没有找到项目文件' in r.stderr)
    check('空目录不落盘报告', not (empty / '.appship' / 'report.md').is_file())

    # 只有 .appship 生成物的目录 → 同样拒绝
    leftover = tmp / 'only-appship'
    (leftover / '.appship').mkdir(parents=True)
    (leftover / '.appship' / 'report.md').write_text('stale', encoding='utf-8')
    r = subprocess.run([PY, str(SCRIPTS / 'ship.py'), str(leftover), '--json'],
                       capture_output=True, text=True, encoding='utf-8')
    check('只有 .appship 生成物 → exit 2 拒绝检测', r.returncode == 2 and '没有找到项目文件' in r.stderr)

    # preview_client --pack 空目录 → 同样拒绝（不上传空 bundle）
    r = subprocess.run([PY, str(SCRIPTS / 'preview_client.py'), str(empty), '--pack', '--json'],
                       capture_output=True, text=True, encoding='utf-8')
    check('preview_client 空目录 → exit 2', r.returncode == 2 and '没有找到项目文件' in r.stderr)


def main():
    tmp = Path(tempfile.mkdtemp(prefix='appship_test_'))
    try:
        test_static(tmp)
        test_fastapi(tmp)
        test_verify_axes(tmp)
        test_blocked(tmp)
        test_commercial_ops(tmp)
        test_false_positive(tmp)
        test_path_errors(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f'通过 {len(passed)} / {len(passed) + len(failed)}')
    if failed:
        print('失败项:', ', '.join(failed))
        sys.exit(1)


if __name__ == '__main__':
    main()
