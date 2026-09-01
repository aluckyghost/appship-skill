# AppShip · AI 启航上线助手

AI 帮你把软件做出来，AppShip 负责让它安全地跑到公网。

## 不用学部署

把项目做完以后，直接告诉 AppShip：

**"帮我看看下一步怎么办。"**

AppShip 会先检查项目（安全、能不能跑、怎么部署最省钱），然后根据你的目标带你继续测试、预览或正式上线。

比如这样说：

> "帮我检查一下这个项目。"
>
> "给我生成一个临时地址看看。"
>
> "我要正式上线这个项目。"

**其它说法也可以，直接用你自己的话告诉 AppShip。**

## 它会带你走完这条路

```text
你随便说一句话
      ↓
AppShip 判断你现在的意图
      ↓
基础检查永远先做（安全、运行、部署方式）
      ↓
├─ 想先看看效果 → 免费临时预览（24 小时自动销毁，搜索引擎不收录）
├─ 只想检查     → 体检报告 + 距离正式上线还差几件事
└─ 准备正式上线 → 检查 → 按你的目标（展示/获客/卖产品）给上线方案
                    → 有服务器？有云账号？什么都没有？都能继续
                    → 正式环境操作前一定先跟你确认
```

**你全程不需要提供服务器密码或云账号 AccessKey**——免费预览由 AppShip 平台完成；正式上线走最小权限授权。

## 使用前唯一准备：邀请 key

AppShip Preview 采用邀请制。把平台发给你的 key 存为 `client.json`（放在项目 `.appship/` 目录、skill 目录或用户主目录 `~/.appship/` 任一处）：

```json
{
  "api_url": "https://cp.appship.top",
  "preview_key": "你的邀请key"
}
```

没有 key 也能用：本地安全检查、部署决策、上线报告全部免费。

## 命令行直跑（可选，不开对话时）

```bash
python scripts/ship.py ./your-project          # 一键体检：安全+运行+部署决策+上线清单
python scripts/preview_client.py ./your-project --request    # 创建临时预览
python scripts/preview_client.py ./your-project --list       # 我的预览列表
python scripts/preview_client.py ./your-project --destroy <JOB_ID>  # 销毁预览
```

退出码：`0` = 可预览，`1` = 需人工确认/被阻断（可接入 CI）。

分步执行：`detect_stack.py` / `security_scan.py` / `preflight.py` / `deployment_decision.py` / `generate_docker.py` / `preview_plan.py` / `cloud_auth.py`。

## 默认部署决策（不让所有项目硬上 Docker）

| 项目类型 | 预览方式 | Docker |
|---|---|---|
| React/Vue/Vite 静态站 | 静态托管（OSS/COS） | 否 |
| 静态前端 + 简单函数 | 静态托管 + Function | 通常否 |
| Next SSR / Node API | 容器（资源限额） | 是 |
| FastAPI/Flask/Django | 容器（资源限额） | 是 |
| 多服务/Worker/Redis | Compose / 专用节点 | 是 |

正式上线建议：静态项目 OSS + CDN（约 ¥10/月起，不用买服务器）；动态项目按需 2C4G 起步。

## 安全原则

- 任何预览/上线之前先做安全检查（CRITICAL 直接阻断，高危需人工确认）
- 绝不自动上传 `.env`、私钥、云凭证到预览环境
- 预览容器有 CPU/内存/进程硬限制 + 网络隔离（禁止访问云 Metadata、内网、SMTP25）
- 预览有 TTL 自动销毁 + 孤儿资源对账
- 正式环境操作（买资源/改 DNS/部署）执行前必须用户确认

## 商业链路

```text
开源 Skill → 免费检查 → 免费 Preview（平台资源，24 小时）
→ 用户满意 → 正式上线（客户自有云账号，RAM 最小授权）
→ 长期托管收费（宇视星托管账号可选，按月计费）
```

需要正式上线 / 国内备案 / 支付接入 / 长期托管？
联系宇视星（iai66.com）｜企业微信：小叮
宇视星专业帮助 AI 做出来的应用 完成正式上线、云部署、安全检查和后续稳定运行。

## 目录

```text
appship-skill/
├── SKILL.md                 # Agent 对话决策流程 + 8 步 Workflow
├── README.md
├── config/
│   └── preview-policy.example.json
├── docs/
│   ├── CLOUD_AUTH.md
│   └── PREVIEW_ARCHITECTURE.md
├── scripts/                 # 11 个脚本（检测/安全/决策/打包/预览客户端）
└── tests/
    └── smoke_test.py        # 72 项冒烟测试
```
