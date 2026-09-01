#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report.py — AppShip v0.4.1 / 报告生成器

把各步检查结果组装成 v0.5 §9 要求的两份输出:
  report.md    人类可读 + Action Plan
  report.json  机器可读（Control Plane / IDE 插件消费）

Action Plan 五段结构:
  已具备 / 必须处理 / 部署建议 / 商业化还缺 / 下一步
顶层带"上线准备度百分比"。

用法（被 ship.py 调用；也可单独测试）:
    python scripts/report.py <report_json_path> <output_dir>
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BRAND = 'AppShip'
BRAND_CN = 'AI 启航上线助手'
# 用户摘要层 CTA：两行价值主张（品牌与官网绑定，首次提及即可理解）（BLOCKED/失败场景不展示）
CONTACT = (
    'AI 帮你把产品做出来，\n'
    '宇视星（iai66.com）负责让它真正上线并持续稳定运行。'
)

STATUS_TEXT = {
    'BLOCKED': '⛔ BLOCKED — 存在阻断级安全问题，禁止上线',
    'REVIEW_REQUIRED': '⚠️ REVIEW_REQUIRED — 有高危项需人工确认',
    'PREVIEW_READY': '✅ PREVIEW_READY — 安全检查通过，可创建免费 Preview',
}


def readiness_scores(report: dict) -> dict:
    """评分模型 v2: 五分项 + 适用性(N/A) + 加权总分。

    先判断项目目标再评分——不适用项记 N/A 并从总分剔除，
    避免"纯展示官网因没做支付/登录被扣分"。
    BLOCKED 直接 0 分。
    """
    s = report.get('steps', {})
    if report.get('status') == 'BLOCKED':
        return {'overall': 0, 'blocked': True}

    # 安全分（counts 已剔除复核误报；文档示例已降级 LOW）
    counts = s.get('security', {}).get('counts', {})
    security = max(0, 100 - counts.get('HIGH', 0) * 25
                   - counts.get('MEDIUM', 0) * 5 - counts.get('LOW', 0))

    # Preview 分 = Preflight 就绪度（现在能不能跑起来）
    preview = s.get('preflight', {}).get('readiness_score', 0)

    # 部署分 = 部署路径是否明确
    dec = s.get('decision', {})
    deployment = 40 if dec.get('type') == 'MANUAL_REVIEW' else 100

    # 运维分
    operations = s.get('operations', {}).get('ops_score', 0)

    # 商业分（不适用 → None → N/A）
    comm = s.get('commercial', {})
    commercial = comm.get('commercial_score') if comm.get('applicable') else None

    # 加权总分（N/A 项权重重分配）
    weights = [('security', security, 0.35), ('preview', preview, 0.25),
               ('deployment', deployment, 0.15), ('operations', operations, 0.15),
               ('commercial', commercial, 0.10)]
    total_w = sum(w for _, v, w in weights if v is not None)
    overall = round(sum(v * w for _, v, w in weights if v is not None) / total_w) if total_w else 0

    return {
        'security': security,
        'preview': preview,
        'deployment': deployment,
        'operations': operations,
        'commercial': commercial,  # None = N/A
        'overall': min(100, overall),
    }


def readiness_percent(report: dict) -> int:
    """上线准备度总分（v2 加权模型；兼容旧调用方）。"""
    return readiness_scores(report).get('overall', 0)


# ---------- 用户视角 headline（供终端摘要 / preview_client 消费） ----------

def hard_launch_count(report: dict) -> int:
    """距离正式上线还差几件"硬"事（launch_needed=必须项；suggested 不计）。"""
    plan = build_action_plan(report)
    return len(plan.get('launch_needed', []))


def headline(report: dict) -> dict:
    """三个最有冲击力的数字: 严重风险数 / 上线准备度 / 距正式上线差距。

    readiness 保留在 json 供技术报告/IDE 消费；用户摘要层不展示百分比。
    """
    counts = report.get('steps', {}).get('security', {}).get('counts', {})
    return {
        'risk': counts.get('CRITICAL', 0) + counts.get('HIGH', 0),
        'readiness': readiness_scores(report).get('overall', 0),
        'todo': hard_launch_count(report),
    }


def build_action_plan(report: dict) -> dict:
    """v0.5 §9.1 五段结构。"""
    s = report['steps']
    plan = {}

    # 1. 已具备
    have = []
    det = s.get('detect', {})
    if det.get('runtime') in ('static_html', 'static_build'):
        have.append('前端静态构建就绪，可直接部署 OSS/CDN')
    elif det.get('runtime'):
        have.append(f"可运行服务（{det.get('runtime')}）")
    for c in s.get('preflight', {}).get('checks', []):
        if c['status'] == 'ok':
            have.append(c['detail'])
    for o in s.get('operations', {}).get('ok', []):
        have.append(f"{o['item']}: {o['detail']}")
    plan['ready'] = have[:12]

    # 2. 必须处理（按严重级排序）
    must = []
    for f in sorted(s.get('security', {}).get('findings', []),
                    key=lambda x: {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}[x['severity']]):
        must.append(f"[{f['severity']}] {f['file']}:{f['line']} {f['desc']}")
    for c in s.get('preflight', {}).get('checks', []):
        if c['status'] == 'warn':
            must.append(c['detail'])
    plan['must_fix'] = must[:15]

    # 3. 部署建议
    dec = s.get('decision', {})
    deploy = []
    if dec:
        deploy.append(f"部署类型: {dec['type']} — {dec.get('reason', '')}")
        deploy.append(f"Docker: {'需要' if dec.get('docker_required') else '不需要'}")
        deploy.append(f"最低资源: {dec.get('resources', '')}")
        if dec.get('db_advice'):
            deploy.append(f"数据库: {dec['db_advice']}")
        if dec.get('redis_advice'):
            deploy.append(f"Redis: {dec['redis_advice']}")
        deploy.append(f"地域: {dec.get('region_advice', '')}")
    plan['deploy'] = deploy

    # 4. 商业化还缺（不适用时明确 N/A，不列支付/登录等无关项）
    comm = s.get('commercial', {})
    biz = []
    if comm.get('applicable'):
        if comm.get('recommended') == 'china':
            for g in comm.get('china_gaps', []):
                biz.append(f"[国内] {g['item']}: {g['need']}")
        elif comm.get('recommended') == 'global':
            for g in comm.get('global_gaps', []):
                biz.append(f"[海外] {g['item']}: {g['need']}")
        else:
            for g in comm.get('china_gaps', []):
                biz.append(f"[国内] {g['item']}: {g['need']}")
            for g in comm.get('global_gaps', []):
                biz.append(f"[海外] {g['item']}: {g['need']}")
        # 运维缺口并入
        for g in s.get('operations', {}).get('gaps', []):
            biz.append(f"[运维] {g['item']}: {g['need']}")
    else:
        biz.append(f"○ Commercial: N/A — {comm.get('note', '未检测到商业化需求')}")
        for g in s.get('operations', {}).get('gaps', []):
            biz.append(f"[运维] {g['item']}: {g['need']}")
    plan['business_gaps'] = biz[:15]

    # 5. 正式上线还需要（分级: launch_needed=必须, launch_suggested=建议）
    det = s.get('detect', {})
    dec = s.get('decision', {})
    if det.get('runtime') in ('static_html', 'static_build'):
        # ICP 备案按部署区域动态计入：明确大陆市场 → 必须项；否则不进计数，
        # 单独一行说明（icp_note）——口径：部署在大陆节点才需要，不是"面向大陆用户就需要"
        plan['launch_needed'] = ['正式域名', 'HTTPS 证书（免费即可，浏览器会显示安全连接）']
        plan['launch_suggested'] = ['网站访问统计', '可用性监控']
        if comm.get('recommended') == 'china':
            plan['launch_needed'].append('中国大陆服务器 → ICP 备案')
        else:
            plan['icp_note'] = '如果使用中国大陆节点正式对外提供网站服务，还需要按要求完成 ICP 备案。'
    else:
        market = comm.get('recommended', 'china')
        gaps_list = comm.get('china_gaps' if market != 'global' else 'global_gaps', [])
        plan['launch_needed'] = [f"{g['item']}（{g['need']}）" for g in gaps_list]
        plan['launch_needed'].append('HTTPS 证书 + 自动续期')
        plan['launch_suggested'] = ['网站访问统计', '可用性监控']
    plan['launch_needed'] = plan['launch_needed'][:10]

    # 6. 下一步（语境化：按状态 + 商业适用性给出路径）
    status = report.get('status')
    nxt = []
    if status == 'BLOCKED':
        nxt.append('1. 修复上述 CRITICAL 安全问题（运行 ship.py --fix 可自动处理部分项）')
        nxt.append('2. 重新运行 python scripts/ship.py 检查')
    elif status == 'REVIEW_REQUIRED':
        nxt.append('1. 确认/修复 HIGH 安全发现（确认为误报可用 security_scan.py --mark-fp 沉淀复核结论）')
        nxt.append('2. 重新运行检查至 PREVIEW_READY')
        nxt.append('3. 创建免费 Preview 验证实际运行效果')
    else:
        nxt.append('1. 创建免费 Preview（运行 preview_client.py --request）并验收')
        if comm.get('applicable'):
            nxt.append('2. 按"正式上线还需要"补齐缺口（域名/备案周期长，尽早启动）')
            nxt.append('3. 修复/确认安全项后按部署建议上生产')
        else:
            nxt.append('2. 只做展示/内部用途：配正式域名 + HTTPS 即可上线')
            nxt.append('3. 准备商业化时：再评估登录/支付等功能（AppShip 重新体检）')
    nxt.append('4. 正式上线/备案/支付接入 可联系宇视星（iai66.com）协助')
    plan['next_steps'] = nxt

    return plan


def render_md(report: dict, preview: dict = None) -> str:
    """生成 report.md — 13 节完整技术报告（Production Readiness Report）。

    preview: 可选，Preview 运行信息（由 preview_client 注入）:
        {url, job_id, status, expires_at, ttl_hours, deploy_mode, host_port, ...}
    """
    scores = readiness_scores(report)
    pct = scores.get('overall', 0)
    head = headline(report)
    plan = build_action_plan(report)
    s = report['steps']
    det = s.get('detect', {})
    dec = s.get('decision', {})
    sec = s.get('security', {})
    comm = s.get('commercial', {})
    ops = s.get('operations', {})
    pf = s.get('preflight', {})
    svc = det.get('services', {})
    is_static = det.get('runtime') in ('static_html', 'static_build')
    c = sec.get('counts', {})
    L = []

    L.append(f'# {BRAND} 完整技术报告')
    L.append('')
    L.append(f'> 项目：{report["project"]}  ')
    L.append(f'> 报告类型：Production Readiness Report  ')
    L.append(f'> 当前状态：{report.get("status", "-")}  ')
    L.append(f'> 生成时间：{datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")}')
    L.append('')

    # ---------- 1. 检测结论 ----------
    L.append('## 1. 检测结论')
    L.append('')
    L.append('| 项目 | 结果 |')
    L.append('|---|---|')
    L.append(f'| 严重安全风险 | {c.get("CRITICAL", 0) + c.get("HIGH", 0)} |')
    L.append(f'| 中危/低危 | {c.get("MEDIUM", 0)} / {c.get("LOW", 0)} |')
    L.append(f'| 是否阻断 Preview | {"是" if report.get("status") == "BLOCKED" else "否"} |')
    L.append(f'| Preview 状态 | {(preview or {}).get("status", "未创建")} |')
    L.append(f'| 推荐部署方式 | {dec.get("type", "-")} |')
    L.append(f'| Docker | {"需要" if dec.get("docker_required") else "不需要"} |')
    L.append(f'| 数据库 | {svc.get("database") or "不需要"} |')
    L.append(f'| Redis | {svc.get("redis") or "不需要"} |')
    L.append(f'| 正式上线准备度 | {pct}%（按当前用途，距正式上线还差 {head["todo"]} 项） |')
    L.append('')
    blocked = report.get('status') == 'BLOCKED'
    L.append(f'**结论：{"存在阻断级安全问题，修复后重新检测。" if blocked else "当前项目可以继续进行公网预览和正式上线准备。"}**')
    L.append('')

    # ---------- 2. 项目识别结果 ----------
    L.append('## 2. 项目识别结果')
    L.append('')
    L.append('### 项目结构')
    L.append('')
    L.append(f'- 技术栈：{det.get("language", "-")} / {det.get("runtime", "-")}')
    L.append(f'- 前端框架：{", ".join(det.get("frameworks", [])) or "无"}')
    L.append(f'- Runtime：{"无长期运行 Runtime" if is_static else det.get("runtime", "-")}')
    L.append(f'- 数据库：{svc.get("database") or "无"}')
    L.append(f'- Redis：{svc.get("redis") or "无"}')
    L.append(f'- Dockerfile：{"非必需" if is_static else ("需要（可由 AppShip 生成）")}')
    L.append('')
    if is_static:
        L.append('### AppShip 判断')
        L.append('')
        L.append('该项目不存在常驻后端服务，因此不建议使用 ECS + Docker 作为默认正式部署方案。')
        L.append('')
        L.append('推荐：')
        L.append('')
        L.append('**静态文件 → OSS / COS / Static Hosting → CDN → 正式域名**')
        L.append('')

    # ---------- 3. 安全检测 ----------
    L.append('## 3. 安全检测')
    L.append('')
    L.append('### 风险汇总')
    L.append('')
    L.append('| 风险等级 | 数量 | 处理结果 |')
    L.append('|---|---:|---|')
    L.append(f'| Critical | {c.get("CRITICAL", 0)} | {"阻断" if c.get("CRITICAL") else "通过"} |')
    L.append(f'| High | {c.get("HIGH", 0)} | {"待人工确认" if c.get("HIGH") else "通过"} |')
    L.append(f'| Medium | {c.get("MEDIUM", 0)} | - |')
    L.append(f'| Low | {c.get("LOW", 0)} | - |')
    if sec.get('reviewed'):
        L.append(f'| 已人工复核误报 | {sec["reviewed"]} | 放行（不计入风险） |')
    if sec.get('doc_downgraded'):
        L.append(f'| 文档示例降级 | {sec["doc_downgraded"]} | 降级为 LOW |')
    L.append('')
    L.append(f'当前{"存在阻断级安全问题" if blocked else "没有发现阻止 Preview 的真实高危安全问题"}。')
    L.append('')

    # 处置记录（S-xxx 编号）
    dispositions = [f for f in sec.get('findings', [])
                    if f.get('reviewed') or f.get('context') == 'documentation_example']
    if dispositions:
        L.append('### 已处置问题')
        L.append('')
        for i, f in enumerate(dispositions, 1):
            if f.get('reviewed'):
                final, action = 'FALSE_POSITIVE', '放行'
                reason = '人工复核确认为误报，不属于项目实际使用的凭据。'
            else:
                final, action = 'DOCUMENTATION_EXAMPLE', '降级'
                reason = '该内容位于文档/教学示例上下文中，不属于真实运行配置。'
            orig = f.get('original_severity', f.get('severity', 'HIGH'))
            L.append(f'#### S-{i:03d}：{f.get("rule", "finding")}')
            L.append('')
            L.append(f'- 文件：`{f["file"]}`（第 {f.get("line", 0)} 行）')
            L.append(f'- 初始等级：{orig}')
            L.append(f'- 最终状态：{final}')
            L.append(f'- 处理结果：{action}')
            L.append(f'- 原因：{reason}')
            L.append('')

    # ---------- 4. Production Readiness ----------
    L.append('## 4. Production Readiness')
    L.append('')
    L.append('### 已满足')
    L.append('')
    for ck in pf.get('checks', []):
        if ck.get('status') == 'ok':
            L.append(f'- [x] {ck.get("detail", ck.get("name", ""))}')
    if is_static:
        L.append('- [x] 无后端 Runtime 依赖')
        L.append('- [x] 无数据库 / Redis 依赖')
        L.append('- [x] 无长期运行进程，可以使用静态托管')
    L.append('')
    L.append('### 正式上线仍需确认')
    L.append('')
    for item in plan.get('launch_needed', []):
        L.append(f'- [ ] {item}')
    L.append('')

    # ---------- 5. 部署决策 ----------
    L.append('## 5. 部署决策')
    L.append('')
    L.append('### 推荐模式')
    L.append('')
    L.append(f'`{dec.get("type", "-")}`')
    L.append('')
    if is_static:
        L.append('### 推荐架构')
        L.append('')
        L.append('```text')
        L.append('Static Files')
        L.append('→ OSS / COS / Static Hosting')
        L.append('→ CDN')
        L.append('→ Domain + HTTPS')
        L.append('```')
        L.append('')
        L.append('### 为什么不推荐 Docker')
        L.append('')
        L.append('当前项目只是静态文件，不存在需要持续运行的服务端进程。使用 Docker / ECS 会额外产生：')
        L.append('')
        L.append('- 服务器费用 / 系统维护 / Docker 维护 / 安全更新 / CPU 内存常驻成本')
        L.append('')
        L.append('**静态托管是当前项目成本最低、维护最简单的正式部署方式。**')
        L.append('')
    else:
        L.append(f"- 理由：{dec.get('reason', '-')}")
        L.append(f"- 最低资源：{dec.get('resources', '-')}")
        L.append(f"- 地域建议：{dec.get('region_advice', '-')}")
        if dec.get('db_advice'):
            L.append(f"- 数据库：{dec['db_advice']}")
        if dec.get('redis_advice'):
            L.append(f"- Redis：{dec['redis_advice']}")
        L.append('')

    # ---------- 6. Preview 验证结果 ----------
    L.append('## 6. Preview 验证结果')
    L.append('')
    if preview:
        L.append('| 项目 | 当前值 |')
        L.append('|---|---|')
        L.append(f'| Preview 状态 | {preview.get("status", "-")} |')
        L.append(f'| Preview URL | `{preview.get("url", "-")}` |')
        L.append(f'| Job ID | `{preview.get("job_id", "-")}` |')
        L.append(f'| TTL | {preview.get("ttl_hours", "-")} 小时（到期自动销毁） |')
        L.append(f'| 搜索引擎收录 | noindex（不会被收录） |')
        if preview.get('deploy_mode') and preview['deploy_mode'] != 'STATIC':
            L.append(f'| 容器 | appship-{preview.get("job_id", "")}（独立网络 + 资源限额） |')
            L.append(f'| 端口映射 | 127.0.0.1:{preview.get("host_port", "-")} → 容器内 {preview.get("container_port", "-")} |')
        else:
            L.append('| Docker Container | 不使用（静态托管） |')
        L.append('')
        L.append('### 生命周期')
        L.append('')
        L.append('Preview 到期或手动销毁后将执行：')
        L.append('')
        if preview.get('deploy_mode') == 'STATIC':
            L.append('1. 删除临时静态文件')
            L.append('2. 删除 Preview 路由')
            L.append('3. Job 状态更新为 `EXPIRED` / `DESTROYED`')
            L.append('4. 清理临时 Workspace 与上传 bundle')
        else:
            L.append('1. 停止并删除容器 / 独立网络 / 镜像')
            L.append('2. 删除 Preview 路由（Caddy）')
            L.append('3. Job 状态更新为 `EXPIRED` / `DESTROYED`')
            L.append('4. 清理临时 Workspace 与上传 bundle')
        L.append('')
    else:
        L.append('尚未创建 Preview。运行 `preview_client.py <project> --request` 可创建免费临时预览（默认 24 小时，到期自动销毁，noindex）。')
        L.append('')

    # ---------- 7. 商业化检查 ----------
    L.append('## 7. 商业化检查')
    L.append('')
    if comm.get('applicable'):
        L.append('当前项目被识别为：**商业型项目**（已检测到登录/支付/邮件/数据库等特征）。')
        L.append('')
        market = comm.get('recommended', 'china')
        gaps = comm.get('china_gaps' if market != 'global' else 'global_gaps', [])
        L.append(f'正式运营前需补齐（{"国内" if market != "global" else "海外"}口径，共 {len(gaps)} 项）：')
        L.append('')
        for g in gaps:
            L.append(f'- {g["item"]}：{g["need"]}')
        L.append('')
    else:
        L.append('当前项目被识别为：**展示型 / 官网型项目**')
        L.append('')
        L.append('因此以下能力当前不是强制上线条件：')
        L.append('')
        L.append('- 用户登录 / 在线支付 / 数据库 / 订单系统 / 短信服务')
        L.append('')
        L.append('如果后续项目目标变为 SaaS、会员系统、在线销售、付费服务，则需要重新进行 Commercial Readiness 检测。')
        L.append('')

    # ---------- 8. 运维检查 ----------
    L.append('## 8. 运维检查')
    L.append('')
    L.append('### 当前需要')
    L.append('')
    for g in ops.get('gaps', []):
        L.append(f'- {g["item"]}：{g["need"]}')
    L.append('')
    if is_static:
        L.append('### 当前不需要')
        L.append('')
        L.append('- 应用进程监控 / Docker 容器监控 / Redis 监控 / 数据库备份 / Worker 监控')
        L.append('')
        L.append('原因：该项目没有动态服务和数据存储层。')
        L.append('')

    # ---------- 9. 正式上线建议 ----------
    L.append('## 9. 正式上线建议')
    L.append('')
    L.append('推荐执行顺序：')
    L.append('')
    order = list(plan.get('launch_needed', [])) + list(plan.get('launch_suggested', []))
    if is_static:
        order = ['确认正式域名', '确认正式部署区域', '如部署在中国大陆，处理 ICP 备案',
                 '部署静态文件到 OSS / COS / Static Hosting', '配置 CDN', '配置 HTTPS',
                 '配置访问统计', '配置基础可用性监控']
    for i, item in enumerate(order, 1):
        L.append(f'{i}. {item}')
    L.append('')

    # ---------- 10. 云权限说明 ----------
    L.append('## 10. 云权限说明')
    L.append('')
    L.append('### Platform Preview')
    L.append('')
    L.append('当前 Preview 由 AppShip Platform 完成。用户无需提供：云账号密码 / AccessKey / SecretKey / 服务器 Root 密码。')
    L.append('')
    L.append('### Customer Production')
    L.append('')
    L.append('正式部署到客户云环境时，建议采用：')
    L.append('')
    L.append('- RAM / IAM 最小权限授权')
    L.append('- 临时凭证')
    L.append('- 可撤销授权')
    L.append('')
    L.append('不建议要求客户直接提供主账号密码。')
    L.append('')

    # ---------- 11. AppShip 生成文件 ----------
    L.append('## 11. AppShip 生成文件')
    L.append('')
    out = report.get('outputs', {})
    L.append(f'- 用户报告（技术报告）：`{out.get("md", "<project>/.appship/report.md")}`')
    L.append(f'- 结构化报告：`{out.get("json", "<project>/.appship/report.json")}`')
    L.append('- 复核沉淀：`<project>/.appship/security-review.json`（如有）')
    L.append('')

    # ---------- 12. 最终状态 ----------
    L.append('## 12. 最终状态')
    L.append('')
    L.append(f'**{report.get("status", "-")}**')
    L.append('')
    if blocked:
        L.append(f'- {report.get("reason", "")}')
    else:
        L.append('- 当前没有阻断 Preview 的严重安全风险')
        L.append('- 项目可以正常公网预览')
        L.append('- 部署模式已确认')
        L.append('- 可以进入正式上线准备阶段')
    L.append('')

    # ---------- 13. 后续支持 ----------
    L.append('## 13. 后续支持')
    L.append('')
    if blocked:
        # BLOCKED 不展示销售 CTA，只指路
        L.append('请先按第 3 节修复阻断级安全问题，重新运行检查后再进入上线准备。')
    else:
        L.append('如需正式上线、云部署、安全整改或长期托管支持，可联系宇视星（iai66.com）。')
    L.append('')
    return '\n'.join(L)


def save_reports(report: dict, out_dir: Path, preview: dict = None) -> tuple:
    """落盘 report.json + report.md 到 out_dir（通常为 project/.appship/）。

    preview: 可选 Preview 运行信息，注入 report.md 第 6 节 + report.json。
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    full = dict(report)
    full['readiness_percent'] = readiness_percent(report)
    full['readiness_scores'] = readiness_scores(report)
    full['headline'] = headline(report)
    full['action_plan'] = build_action_plan(report)
    if preview:
        full['preview_runtime'] = preview
    full['generated_at'] = datetime.now(timezone.utc).isoformat()

    jpath = out_dir / 'report.json'
    jpath.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding='utf-8')

    mpath = out_dir / 'report.md'
    mpath.write_text(render_md(report, preview), encoding='utf-8')

    return jpath, mpath


def main():
    if len(sys.argv) != 3:
        print('用法: python scripts/report.py <report_json_path> <output_dir>', file=sys.stderr)
        sys.exit(2)
    report = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    out = Path(sys.argv[2])
    j, m = save_reports(report, out)
    print(f'已生成: {j}\n已生成: {m}')


if __name__ == '__main__':
    main()
