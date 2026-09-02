#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ship.py — AppShip v0.4.1 主入口

按顺序编排: 识别 → 安全检查 → Preflight → 部署决策 → 商业就绪 → 运维就绪 →
生成配置 → Preview 方案 → 云授权建议 → 最终结论(BLOCKED / REVIEW_REQUIRED / PREVIEW_READY)。
完成后落盘 report.md + report.json 到 <project>/.appship/。

安全优先: CRITICAL 发现立即阻断，不再继续后续步骤。

用法:
    python scripts/ship.py /path/to/project [--json] [--save] [--fix]
        [--auth-provider aliyun] [--auth-mode platform]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from detect_stack import detect, is_project_dir
from security_scan import scan as security_scan
from preflight import preflight
from deployment_decision import decide
from commercial_readiness import readiness as commercial_readiness
from operations_readiness import operations
from preview_plan import plan as preview_plan
from cloud_auth import ADVICE
from report import save_reports, build_action_plan, headline, CONTACT

BRAND = 'AppShip'
BRAND_CN = 'AI 启航上线助手'


def run(root: Path, auth_provider='aliyun', auth_mode='platform', log=None) -> dict:
    """执行 8 步检测。log(msg) 为过程日志回调（None=静默，供 --json/库调用）。"""
    def say(msg):
        if log:
            log(msg)

    report = {'brand': f'{BRAND} ({BRAND_CN})', 'project': root.name, 'steps': {}}

    # Step 1 识别技术栈
    say('正在识别项目...')
    stack = detect(root)
    report['steps']['detect'] = {'language': stack['language'], 'runtime': stack['runtime'],
                                 'frameworks': stack['frameworks'], 'target_market': stack['target_market'],
                                 'services': stack.get('services', {}),
                                 'features': stack.get('features', {})}
    fw = '、'.join(stack['frameworks'][:3]) or '无框架'
    say(f"✓ 项目识别完成（{stack['language']} / {fw}）")

    # Step 2 安全检查（CRITICAL 直接阻断）
    say('正在进行安全检查...')
    security = security_scan(root)
    report['steps']['security'] = {'status': security['status'], 'counts': security['counts'],
                                   'findings': security['findings'],
                                   'reviewed': security.get('reviewed', 0),
                                   'doc_downgraded': security.get('doc_downgraded', 0)}
    if security['counts']['CRITICAL'] > 0:
        say(f"✗ 发现 {security['counts']['CRITICAL']} 个阻断级风险，检测终止")
        report['status'] = 'BLOCKED'
        report['reason'] = f"存在 {security['counts']['CRITICAL']} 个 CRITICAL 安全发现，已阻断。修复后重新运行。"
        return report
    notes = []
    if security.get('reviewed'):
        notes.append(f"{security['reviewed']} 项已复核为误报")
    if security.get('doc_downgraded'):
        notes.append(f"{security['doc_downgraded']} 项文档示例已降级")
    note = f"（{'，'.join(notes)}）" if notes else ''
    if security['counts']['HIGH'] > 0:
        say(f"△ 发现 {security['counts']['HIGH']} 个高风险项，需人工确认{note}")
    else:
        say(f"✓ 未发现严重风险{note}")

    # Step 3 Preflight
    say('正在做运行预检...')
    pf = preflight(root)
    report['steps']['preflight'] = {'readiness_score': pf['readiness_score'], 'checks': pf['checks']}
    say(f"✓ 运行预检完成（{pf['readiness_score']}%）")

    # Step 4 部署决策
    say('正在判断部署方式...')
    decision = decide(root)
    report['steps']['decision'] = {
        'type': decision['type'], 'reason': decision['reason'],
        'docker_required': decision['docker_required'],
        'resources': decision['resources'],
        'db_advice': decision['db_advice'],
        'redis_advice': decision['redis_advice'],
        'region_advice': decision['region_advice'],
    }
    dep_txt = '静态托管（无需单独购买和维护服务器）' if decision['type'] == 'STATIC' else decision['type']
    say(f"✓ 推荐部署方式: {dep_txt}")

    # Step 5 商业就绪 + 运维就绪
    say('正在评估商业/运维就绪度...')
    report['steps']['commercial'] = commercial_readiness(root)
    report['steps']['operations'] = operations(root)
    say('✓ 评估完成')

    # Step 6 部署配置（不自动写盘，仅提示）
    report['steps']['artifacts'] = {
        'note': '容器类项目可运行 generate_docker.py 生成 Dockerfile/.dockerignore/compose',
        'skipped': decision['type'] == 'STATIC',
    }

    # Step 7 Preview 方案
    pv = preview_plan(root)
    report['steps']['preview'] = pv['preview']

    # Step 8 云授权建议
    auth = ADVICE.get((auth_provider, auth_mode))
    report['steps']['cloud_auth'] = {'provider': auth_provider, 'mode': auth_mode,
                                     'path': auth['path'] if auth else '-'}

    # Step 9 最终结论
    if security['counts']['HIGH'] > 0:
        report['status'] = 'REVIEW_REQUIRED'
        report['reason'] = f"存在 {security['counts']['HIGH']} 个 HIGH 安全发现，人工确认/修复后可进入 Preview。"
    else:
        report['status'] = 'PREVIEW_READY'
        report['reason'] = '安全检查通过，可进入 Preview。'
    return report


def render(report: dict) -> str:
    """检测完成后的用户结果摘要（仅两层之一；过程日志由 run(log=) 实时输出）。"""
    s = report['steps']
    head = headline(report)
    plan = build_action_plan(report)
    sec = s.get('security', {})
    comm = s.get('commercial', {})
    W = 60
    L = []
    L.append('=' * W)
    L.append(f'{BRAND} · {report["project"]}')
    L.append('=' * W)
    L.append('')

    if report['status'] == 'BLOCKED':
        L.append('⛔ 现在还不建议上线')
        L.append('')
        L.append(f"发现 {head['risk']} 个必须先处理的问题：")
        order = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
        for f in sorted(sec['findings'], key=lambda x: order[x['severity']])[:5]:
            L.append(f"   [{f['severity']}] {f['file']}:{f['line']} {f['desc']}")
        L.append('')
        L.append('先处理这些问题，再继续测试或正式上线会更安全。')
        L.append('')
        L.append('→ 查看修复建议（ship.py --fix 可自动处理部分项）')
        L.append('→ 查看完整技术报告（.appship/report.md）')
        L.append('')
        L.append('=' * W)
        return '\n'.join(L)

    if report['status'] == 'REVIEW_REQUIRED':
        L.append('⚠️ 有几项需要你确认')
        L.append('')
        L.append(f"🟡 严重安全风险：{head['risk']} 项（需人工确认）")
        for f in sec['findings']:
            if f['severity'] == 'HIGH':
                L.append(f"   [HIGH] {f['file']}:{f['line']} {f['desc']}")
        L.append('其中一些可能是真实风险，也可能只是文档或测试示例。确认后我再继续。')
        L.append('')
        L.append('→ 确认为误报: ship.py --mark-fp 文件:行号:规则名')
        L.append('→ 查看完整技术报告（.appship/report.md）')
        L.append('')
        L.append('-' * W)
        L.append(CONTACT)
        return '\n'.join(L)

    # PREVIEW_READY —— 用户结果摘要
    goal = '具备收款条件' if comm.get('applicable') else '正式上线'
    L.append('🎉 检查完成')
    L.append('')
    L.append(f"🟢 严重安全风险：{head['risk']} 项")
    L.append('✅ 当前状态：已经具备临时验证条件')
    L.append(f"🚀 按当前用途，距离{goal}还差 {head['todo']} 项")
    L.append('')
    needed = plan.get('launch_needed', [])
    if needed:
        L.append(f"还差：{' / '.join(needed)}")
    suggested = plan.get('launch_suggested', [])
    if suggested:
        L.append(f"建议再补：{' / '.join(suggested)}")
    icp_note = plan.get('icp_note')
    if icp_note:
        L.append('')
        L.append(icp_note)
    L.append('')
    L.append('→ 生成公网临时链接（24 小时自动销毁，不会动你的正式环境）')
    L.append('→ 查看完整技术报告（.appship/report.md）')
    L.append('')
    L.append('接下来你可以直接回复：')
    L.append('“先预览” —— 我给你生成一个 24 小时有效的公网临时链接')
    L.append(f'“准备正式上线” —— 我继续带你确定域名、运行环境和{goal}方案')
    L.append('')
    L.append('-' * W)
    L.append(CONTACT)
    L.append('=' * W)
    return '\n'.join(L)


def main():
    parser = argparse.ArgumentParser(description=f'{BRAND} ({BRAND_CN}): 检查→决策→Preview 方案')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--save', action='store_true', help='落盘 .appship/report.md + report.json（默认开启）')
    parser.add_argument('--no-save', action='store_true', help='不落盘报告')
    parser.add_argument('--fix', action='store_true', help='检查后执行安全自动修复（auto_fix.py）')
    parser.add_argument('--auth-provider', default='aliyun', choices=['aliyun', 'tencent', 'linux'])
    parser.add_argument('--auth-mode', default='platform', choices=['platform', 'customer'])
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)
    if not is_project_dir(root):
        print(f'错误: {root} 里没有找到项目文件（目录为空或只有生成物）。'
              f'请确认项目路径，或先把代码放进来。', file=sys.stderr)
        sys.exit(2)

    # 过程日志（--json 静默，供程序消费）；报告随后统一输出
    report = run(root, args.auth_provider, args.auth_mode,
                 log=None if args.json else print)

    if args.fix:
        from auto_fix import auto_fix
        fix_result = auto_fix(root, dry=False)
        report['auto_fix'] = fix_result

    save = not args.no_save
    if save:
        report['outputs'] = {'json': str(root / '.appship' / 'report.json'),
                             'md': str(root / '.appship' / 'report.md')}
        jpath, mpath = save_reports(report, root / '.appship')

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if save:
            print('正在生成报告...')
            print(f"✓ 报告已生成: {root / '.appship' / 'report.md'}")
            print()
        print(render(report))
        if report.get('auto_fix'):
            print(f"自动修复: {report['auto_fix']['changed']} 项（备份在 .appship/backup/）")
    sys.exit(0 if report['status'] == 'PREVIEW_READY' else 1)


if __name__ == '__main__':
    main()
