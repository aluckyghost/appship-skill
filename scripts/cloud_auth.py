#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cloud_auth.py — AppShip v0.4.1 / Step 7: 云授权方式建议

本脚本不执行任何云操作，只输出安全授权路径建议。
原则: Skill 不持有云主账号 AccessKey / root 密码。

用法:
    python scripts/cloud_auth.py --provider aliyun --mode platform
    python scripts/cloud_auth.py --provider aliyun --mode customer
    python scripts/cloud_auth.py --provider tencent --mode customer
    python scripts/cloud_auth.py --provider linux --mode customer
"""

import argparse
import json
import sys

ADVICE = {
    ('aliyun', 'platform'): {
        'title': '平台自有 Preview 资源（阿里云）',
        'path': 'Skill → Deploy API → 阿里云 CLI/SDK → 云资源',
        'steps': [
            'Deploy API Worker 部署在阿里云 ECS',
            '为 ECS 绑定最小权限 RAM Role（EcsRamRole），通过 IMDS 获取临时 STS 凭证',
            '服务器上不保存任何长期 AccessKey',
            '所有云操作记录审计日志（project_id / action / resource_id / result / timestamp）',
        ],
        'forbidden': ['主账号 AccessKey 写入代码或配置', 'AccessKey 提交到 Git'],
    },
    ('aliyun', 'customer'): {
        'title': '客户自有阿里云账号（正式 Production）',
        'path': '客户浏览器/设备码授权 → 授予最小权限 RAM Role → 平台 AssumeRole 临时凭证 → 部署',
        'steps': [
            '客户在 RAM 控制台创建专用 Role，仅授予所需资源权限（ECS/OSS/DNS/SLB）',
            '平台通过 AssumeRole 获取临时 STS 凭证执行部署',
            '客户可随时撤销授权，撤销后平台立即失去权限',
            '正式生产资源归客户主体所有',
        ],
        'forbidden': ['客户在聊天中粘贴主账号 AK/SK', '要求客户提供 root 密码'],
    },
    ('tencent', 'customer'): {
        'title': '客户自有腾讯云账号',
        'path': 'CloudBase CLI 设备码/浏览器授权 → 结构化输出 → 部署',
        'steps': [
            '简单项目: CloudBase（tcb login 设备码授权，适合 Agent/无浏览器环境）',
            '普通/复杂项目: CVM + Docker，授予子账号最小权限',
            '存储用 COS，数据库用 TencentDB，DNS 用 DNSPod',
        ],
        'forbidden': ['长期保存客户主账号 SecretKey'],
    },
    ('linux', 'customer'): {
        'title': '客户自有 Linux 服务器',
        'path': 'Ship Runner（规划中）受控执行，或临时 SSH Key 人工协助',
        'steps': [
            '推荐后续部署 Ship Runner: 出站连接 + 操作白名单 + 完整审计',
            '过渡期可用临时 SSH Key，操作完成立即回收',
            '支持动作: 部署 / 重启 / 日志 / 备份 / 回滚 / 健康检查',
        ],
        'forbidden': ['收集/保存客户 root 密码', '提供无限制远程 Shell'],
    },
}


def main():
    parser = argparse.ArgumentParser(description='AppShip Step 7: 云授权建议')
    parser.add_argument('--provider', required=True, choices=['aliyun', 'tencent', 'linux'])
    parser.add_argument('--mode', required=True, choices=['platform', 'customer'])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    advice = ADVICE[(args.provider, args.mode)]

    if args.json:
        print(json.dumps(advice, ensure_ascii=False, indent=2))
    else:
        print(f"== {advice['title']} ==")
        print(f"授权链路: {advice['path']}")
        print("步骤:")
        for i, s in enumerate(advice['steps'], 1):
            print(f"  {i}. {s}")
        print("禁止:")
        for f in advice['forbidden']:
            print(f"  × {f}")
    return advice


if __name__ == '__main__':
    main()
