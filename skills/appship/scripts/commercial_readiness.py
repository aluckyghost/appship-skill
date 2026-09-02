#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
commercial_readiness.py — AppShip v0.4.1 / Step 5.5: 商业就绪度检查

检查商业化必需项缺口（v0.5 §7）:
  中国大陆: 域名 / ICP 备案 / HTTPS / 手机号登录 / 微信登录 / 短信 /
           微信支付 / 支付宝 / OSS-COS / CDN
  海外: Global Domain / HTTPS / Google OAuth / Email / Stripe / PayPal /
        海外 DB-对象存储 / Analytics

输出缺口清单 + 市场建议，不修改任何文件。

用法:
    python scripts/commercial_readiness.py /path/to/project [--json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from detect_stack import detect


def check_china(root: Path, stack: dict) -> list:
    """大陆商业化缺口检查。"""
    deps = set()
    pkg = root / 'package.json'
    if pkg.is_file():
        try:
            p = json.loads(pkg.read_text(encoding='utf-8', errors='ignore'))
            deps.update((p.get('dependencies') or {}).keys())
            deps.update((p.get('devDependencies') or {}).keys())
        except json.JSONDecodeError:
            pass
    req = root / 'requirements.txt'
    if req.is_file():
        for line in req.read_text(encoding='utf-8', errors='ignore').splitlines():
            name = line.strip().split('==')[0].split('>=')[0].strip().lower()
            if name and not name.startswith('#'):
                deps.add(name)

    gaps = []

    # 支付: 微信支付 / 支付宝
    has_wxpay = bool(deps & {'wechatpay-node-v3', 'wechatpay', 'wechatpay-python', 'wepay'})
    has_alipay = bool(deps & {'alipay-sdk', 'alipay', 'alipay-sdk-python', 'python-alipay-sdk'})
    if not has_wxpay and not has_alipay:
        gaps.append({'item': '支付渠道', 'need': '微信支付 或 支付宝（二选一起步）',
                     'hint': '大陆用户付费必须。个人主体可先用赞赏码/知识星球过渡，企业主体走官方商户号'})

    # 登录: 手机号 / 微信
    has_phone_auth = bool(deps & {'dysmsapi', 'aliyun-python-sdk-dysmsapi', '@alicloud/dysmsapi20170525'})
    has_wechat_auth = bool(deps & {'wechat', 'weixin', 'wx-server-sdk', 'wechat-oauth', 'co-wechat'})
    if not has_phone_auth and not has_wechat_auth:
        gaps.append({'item': '用户登录', 'need': '手机号+验证码 或 微信扫码登录',
                     'hint': '短信需阿里云/腾讯云 SMS 服务（需企业实名）'})

    if not has_phone_auth and not has_wechat_auth:
        pass
    elif not has_phone_auth:
        gaps.append({'item': '短信服务', 'need': 'SMS 验证码通道',
                     'hint': '登录若用手机号，需开通短信服务并申请签名/模板'})

    # 存储 / CDN
    has_oss = bool(deps & {'ali-oss', 'oss2', '@alicloud/oss', 'cos-nodejs-sdk-v5', 'qcloud-cos'})
    if not has_oss:
        gaps.append({'item': '对象存储', 'need': 'OSS/COS（用户上传文件不能放服务器本地盘）',
                     'hint': '有上传/生成文件功能的项目必须'})

    # 基础项（所有大陆上线都要）
    gaps.append({'item': '域名 + ICP 备案', 'need': '自有域名 + 阿里云/腾讯云 ICP 备案',
                 'hint': '备案审核时间以云厂商和当地管局为准，建议尽早启动。服务器须在中国大陆'})
    gaps.append({'item': 'HTTPS', 'need': 'SSL 证书（免费证书即可，浏览器会显示安全连接）',
                 'hint': '微信登录/支付强制要求 HTTPS'})

    return gaps


def check_global(root: Path, stack: dict) -> list:
    """海外商业化缺口检查。"""
    deps = set()
    pkg = root / 'package.json'
    if pkg.is_file():
        try:
            p = json.loads(pkg.read_text(encoding='utf-8', errors='ignore'))
            deps.update((p.get('dependencies') or {}).keys())
            deps.update((p.get('devDependencies') or {}).keys())
        except json.JSONDecodeError:
            pass
    req = root / 'requirements.txt'
    if req.is_file():
        for line in req.read_text(encoding='utf-8', errors='ignore').splitlines():
            name = line.strip().split('==')[0].split('>=')[0].strip().lower()
            if name and not name.startswith('#'):
                deps.add(name)

    gaps = []

    has_stripe = bool(deps & {'stripe', '@stripe/stripe-js', 'stripe-python'})
    has_paypal = bool(deps & {'paypal', '@paypal/checkout-server-sdk', 'paypalrestsdk'})
    if not has_stripe and not has_paypal:
        gaps.append({'item': '支付渠道', 'need': 'Stripe（首选）或 PayPal',
                     'hint': 'Stripe 需海外主体或用 Stripe Atlas；MoR（Paddle/LemonSqueezy）可免主体'})

    has_google = bool(deps & {'google-auth', 'passport-google-oauth', 'google-oauth', 'google-auth-library'})
    has_supabase = bool(deps & {'@supabase/supabase-js', 'supabase'})
    has_auth0 = bool(deps & {'auth0', 'express-openid-connect'})
    if not (has_google or has_supabase or has_auth0):
        gaps.append({'item': '用户登录', 'need': 'Google OAuth（或 Supabase/Auth0 托管登录）',
                     'hint': '海外用户极不接受注册表单，Google 一键登录是标配'})

    has_email = bool(deps & {'resend', '@sendgrid/mail', 'nodemailer', 'sendgrid', 'postmark'})
    if not has_email:
        gaps.append({'item': '邮件服务', 'need': 'Resend / SendGrid / Postmark',
                     'hint': '注册验证、密码重置、订阅通知都依赖'})

    has_analytics = bool(deps & {'posthog', 'mixpanel', 'plausible', '@vercel/analytics', 'analytics'})
    if not has_analytics:
        gaps.append({'item': 'Analytics', 'need': 'PostHog / Plausible / GA4',
                     'hint': '不做埋点就无法验证留存与转化'})

    gaps.append({'item': 'Global Domain + HTTPS', 'need': '.com/.ai 等国际域名 + 自动证书',
                 'hint': '海外部署免备案，Cloudflare 免费套 CDN + SSL'})

    return gaps


# 商业化能力清单: 用户视角"人话"检测（获客/表单/登录/支付/统计）
CONTACT_HINTS = ('mailto:', 'tel:', 'weixin', '微信', '企业微信', 'wpa.qq.com',
                 'whatsapp', 't.me/', '联系电话', '联系我们')
ANALYTICS_HINTS = ('hm.baidu.com', 'googletagmanager', 'gtag(', 'google-analytics',
                   'plausible.io', 'posthog', 'umami')


def capabilities(root: Path) -> list:
    """从代码/页面内容检测商业化能力现状，返回用户可读的能力清单。

    status: ok=已具备 / unsure=需要人工确认 / missing=未配置
    """
    stack = detect(root)
    feat = stack.get('features') or {}

    text = ''
    for p in root.rglob('*'):
        if p.suffix.lower() in ('.html', '.htm', '.md', '.js', '.jsx', '.ts', '.vue', '.tsx') \
                and 'node_modules' not in p.parts and '.git' not in p.parts:
            try:
                text += p.read_text(encoding='utf-8', errors='ignore').lower()
            except OSError:
                pass
            if len(text) > 2_000_000:
                break

    has_contact = any(h in text for h in CONTACT_HINTS)
    has_form = ('<form' in text) or ('contact' in text) or ('咨询' in text) or ('表单' in text)
    has_analytics = any(h in text for h in ANALYTICS_HINTS)

    return [
        {'name': '获客入口', 'status': 'ok' if has_contact else 'unsure',
         'detail': '检测到联系方式（电话/微信/邮箱）' if has_contact else '未检测到显眼的联系方式，需要确认'},
        {'name': '咨询/表单', 'status': 'ok' if has_form else 'unsure',
         'detail': '检测到表单或咨询入口' if has_form else '未检测到表单，需要确认'},
        {'name': '用户登录', 'status': 'ok' if feat.get('auth') else 'missing',
         'detail': '已接入登录依赖' if feat.get('auth') else '未配置'},
        {'name': '在线支付', 'status': 'ok' if feat.get('payment') else 'missing',
         'detail': '已接入支付依赖' if feat.get('payment') else '未配置'},
        {'name': '数据统计', 'status': 'ok' if has_analytics else 'missing',
         'detail': '已接入统计脚本' if has_analytics else '未配置'},
    ]


def readiness(root: Path) -> dict:
    stack = detect(root)
    market = stack['target_market']

    # 商业化适用性: 检测到登录/支付/邮件等商业化特征，或有数据库（用户数据产品）
    # 纯展示型静态站/内部工具 → Commercial = N/A，不参与总评（避免"官网因没支付被扣分"）
    feat = stack.get('features') or {}
    applicable = bool(feat.get('auth') or feat.get('payment') or feat.get('email')
                      or (stack.get('services') or {}).get('database'))

    result = {
        'project': stack['project'],
        'target_market': market,
        'applicable': applicable,
    }

    if market == 'china':
        result['recommended'] = 'china'
        result['china_gaps'] = check_china(root, stack)
    elif market == 'global':
        result['recommended'] = 'global'
        result['global_gaps'] = check_global(root, stack)
    else:
        # 市场未明 → 两边都给，让用户选
        result['recommended'] = 'unknown（依赖未发现明确市场倾向，两个市场清单都提供）'
        result['china_gaps'] = check_china(root, stack)
        result['global_gaps'] = check_global(root, stack)

    if applicable:
        key = 'china_gaps' if result.get('recommended') == 'china' else 'global_gaps'
        result['commercial_score'] = max(0, 100 - 12 * len(result.get(key, [])))
        result['note'] = '已检测到登录/支付/邮件/数据库等商业化特征'
    else:
        result['commercial_score'] = None
        result['note'] = '未检测到登录/支付等商业化需求（展示型/内部型项目，商业项不参与总评）'

    # 商业化能力清单（用户视角人话版，供报告渲染）
    result['capabilities'] = capabilities(root)

    return result


def main():
    parser = argparse.ArgumentParser(description='AppShip: 商业就绪度检查')
    parser.add_argument('project', help='项目路径')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args()

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f'错误: 项目路径不存在 {root}', file=sys.stderr)
        sys.exit(2)

    result = readiness(root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"项目: {result['project']}  市场倾向: {result['target_market']}")
        print(f"建议市场: {result['recommended']}")
        score = result.get('commercial_score')
        print(f"商业化适用性: {'适用' if result['applicable'] else 'N/A'}"
              + (f"（{score}%）" if score is not None else f" — {result['note']}"))
        for market_key, title in (('china_gaps', '中国大陆上线还缺'), ('global_gaps', '海外上线还缺')):
            gaps = result.get(market_key)
            if gaps is None:
                continue
            print()
            print(f'{title}（{len(gaps)} 项）:')
            for i, g in enumerate(gaps, 1):
                print(f"  {i}. {g['item']}: {g['need']}")
                print(f"     提示: {g['hint']}")
    return result


if __name__ == '__main__':
    main()
