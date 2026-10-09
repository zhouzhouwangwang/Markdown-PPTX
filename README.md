# PPTX-SiteTheme —— 结构化 Markdown → 可编辑 PPTX 服务

把**结构化 Markdown 文稿**转成**可编辑 PPTX** 的自包含 HTTP 服务。

- **零 LLM、零外部 API、零网络转换**：渲染 → 转换 → 校验三步全部本机确定性执行，同一份文稿产出的页面 XML 逐字节一致；
- **真 PPTX**：原生文本框与形状，零图片化，PowerPoint / WPS / Keynote 直接编辑；
- **自包含部署**：转换器（钉版 vendor）、Chromium、中文字体全部固化进 Docker 镜像，整目录拷走即可运行。

> 上游引用：核心转换器来自开源项目 **PPTAgent**（`deeppresenter/html2pptx`，MIT，钉版 v1.1.38），详见文末「开源引用与来源声明」。

---

## 1. 功能特性

### 1.1 文稿语法（结构 → 版式）

| 语法 | 映射 |
|---|---|
| `# 标题` | 封面页（必须有） |
| `> 引导语` | 封面副标题 |
| `## 节标题` | 一节一页起（内容超高自动拆「（续）」页） |
| `### 小标题` | 节内小标题（与后续内容**原子分组**，永不跨页分离） |
| `- 要点` / `1. 步骤` | 项目 / 编号列表 |
| `**加粗**` | 真 PPTX 粗体（行内 `<strong>` → `b="1"` 文本 run），可用于标题/要点/图注 |
| `---` | 分隔习惯，可省略 |
| `![角色](路径){选项}` | 图片插入（见 1.2） |

### 1.2 图片系统（四类角色 + 定位语法）

```markdown
![logo](assets/logo.png)                                  ← 页面角标 logo（零配置）
![background](assets/bg.jpg){dim=0.8}                    ← 背景：封面前 = 全局；节内 = 本节局部
![background](assets/bg.jpg){region=content, dim=0.5}    ← 内容区背景（避开顶栏/脚栏）
![figure](assets/fig.jpg){w=40%, dx=10, dy=-5}           ← 流式插图（占排版高度，参与分页）
![figure](assets/left.jpg){half=left}                    ← 左右半宽，相邻成对自动并排（figrow）
![figure](assets/cover.jpg){fill}                        ← 填满本页剩余空间（z=10 前景层）
![figure](assets/cover.jpg){fill, z=0}                   ← 填充背景层（z=0 铺底，不占排版高度）
![贴图](assets/car.png){pin=br, w=30%, dx=-10, z=10}     ← 钉在九宫格锚点，浮层不占排版高度
```

**通用选项**：`w=` 宽度百分比 · `dx/dy=` 像素偏移（右/下为正）· `z=` 层序（≤0 文字下方，>0 文字上方）· `dim=` 压暗（背景专用）。

**多宫格贴图**（`pin=` 支持 `+` 链选择多个格子）：

| 选择形态 | 行为 | 默认 z |
|---|---|---|
| 单格 `pin=br` | 图 contain-fit 贴该格 | 10 |
| **矩形**多格（整列 `tr+r+br`、整行、2×2 块） | 一张图 contain-fit 居中于**外接矩形** | 10 |
| **非矩形**（L/T 形）或**非连续**（四角 `tl+tr+bl+br`） | **碎片蒙版**：虚拟大图按整格 contain-fit，仅选中格显示对应裁片（Pillow 预裁，转换器只见普通图片），中间未选格不显示图片 | 0（永不盖字） |

九宫格键位：`tl t tr / l c r / bl b br`（相对整页、顶栏与脚栏之间的区域）。

### 1.3 自动分页

按内容**真实高度**分页（`max_bullets` 只是每页条数上限）：`###` 小标题与其后续内容原子绑定不跨页；长要点提前拆「（续）」页；若转换器实测字体度量仍溢出，按确定性序列自动降密重排（1.0 → 0.85 → 0.72）。

### 1.4 主题（15 个）

- **手工主题**：`classic`（暖纸金）/ `swiss`（瑞士极简）/ `midnight`（深色）；
- **生成主题** `{style}.{industry}`：`site`（网页导航风）/ `cards`（圆角卡片阵）× 6 行业（`tech`/`finance`/`edu`/`energy`/`gov`/`med`），行业色板取自 ui-ux-pro-max 设计情报，由 `tools/build_themes.py` 离线生成并通过 WCAG AA 对比度审计，线上零生成逻辑。

### 1.5 内置表单（`http://127.0.0.1:19000/`）

Markdown 粘贴 + 实时格式自检 + 分页预览 + 主题预览；**图片插入向导**：上传资产 → 选择类型（贴图/插图/背景/logo）→ 仅显示该类型相关控件（logo 零配置、贴图九宫格含「多选」开关）→ 生成语法插入光标处；支持**自定义右键菜单**（含剪贴板兜底）。

---

## 2. 目录结构

```
pptx-site-theme/
├── app/                          服务代码（FastAPI + 纯标准库渲染器）
│   ├── main.py                   路由：/healthz、/、/v1/decks、/v1/preview、资产上传
│   ├── pipeline.py               编排：渲染 → 转换（重试）→ 校验
│   ├── render.py                 Markdown → HTML：解析/分页/图片定位/背景烘焙/碎片裁切
│   ├── verify.py                 PPTX 结构校验（页数/画布/可编辑/文案完整/非纯图）
│   ├── settings.py               全部配置来自环境变量
│   ├── static/index.html         内置表单单页（插入向导 + 右键菜单 + 预览）
│   └── templates/                15 个主题目录（cover.css + section.css）
├── vendor/html2pptx/             上游转换器（4 文件 + PROVENANCE.md，勿改，见 §10）
├── assets/                       图片素材库（表单上传落点，容器挂载）
├── output/<task_id>/             生成物：request.json / manuscript.md / slides/*.html / answer.pptx / result.json
├── tests/
│   ├── test_units.py             渲染/校验单元测试（无浏览器）
│   └── test_api.py               端到端测试（真实 Chromium 转换，PPTSVC_E2E=1 启用）
├── tools/
│   ├── run-tests.ps1             一键测试入口（加载 test.env）
│   └── build_themes.py           生成主题的离线构建脚本
├── .probe/                       浏览器探针（表单交互/转换器装饰词实测）
├── examples/                     示例稿件 + 各主题成品 PPTX
├── docs/                         深度示例文稿
├── test.env                      宿主测试路径配置（唯一需要按机器改的文件）
├── .test-runtime/                宿主测试运行时（gitignored）：Chromium + playwright 模块
├── Dockerfile                    node20 + python3 + Chromium + Noto CJK 字体
├── docker-compose.yml            开发模式（代码映射 + 热重载）
├── docker-compose.prod.yml       生产模式（代码进镜像，仅挂载 output/assets）
└── requirements.txt / requirements-dev.txt   运行依赖 / 测试依赖（pytest、httpx）
```

---

## 3. 快速开始

### 3.1 Docker（推荐）

```bash
# 生产模式：代码进镜像，仅挂载 output 与 assets
docker compose -f docker-compose.prod.yml up -d --build

# 开发模式：app/ 映射进容器，改 Python/CSS 保存即生效
docker compose up -d --build
```

打开 `http://127.0.0.1:19000/` 即是内置表单。

**镜像设计**：转换器、其 `node_modules`（`/opt/pptsvc`）与 Chromium（`/opt/ms-playwright`）固化在镜像内、位于挂载点之外——宿主平台二进制永不混入，升级转换器 = 重建镜像，可审查。构建默认走国内镜像源（`.env` 可覆盖回官方）。

### 3.2 本机裸跑（node 20+、python 3.11+）

```bash
cd vendor/html2pptx && npm ci && npx playwright install chromium && cd ../..
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 19000
```

> Chromium 非默认位置时设 `PLAYWRIGHT_BROWSERS_PATH`；CDN 不可达时加 `PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`。

---

## 4. 使用方法

### 4.1 表单（图形界面）

1. 粘贴/撰写 Markdown（格式自检会在发送前标出「标记后缺空格」等错误）；
2. 上传图片素材 → 光标定位 → 工具栏「图片」或**右键菜单** → 选类型 → 配参数 → 插入语法；
3. 切换主题实时预览（与转换器同源渲染）；生成为 PPTX 后按校验报告下载。

### 4.2 API

```bash
curl -X POST http://127.0.0.1:19000/v1/decks \
  -H "Content-Type: application/json" \
  -d '{
    "markdown": "# 职业规划展示\n\n> 从初心出发\n\n## 初心起源\n\n- 要点一\n- **重点**：加粗强调\n",
    "aspect_ratio": "16:9",
    "theme": "site.tech"
  }'
```

| 接口 | 用途 |
|---|---|
| `GET /healthz` | 依赖自检（node/转换器/模板/输出目录） |
| `GET /` | 内置表单 |
| `POST /v1/decks` | 生成 PPTX（同步，返回下载地址与校验报告） |
| `POST /v1/preview` | 只渲染不转换：全部页 HTML，毫秒级预览 |
| `GET /v1/decks/{id}` / `GET /v1/decks/{id}/pptx` | 回读历史 / 下载 |

请求参数：`markdown`（必填）、`aspect_ratio`（`16:9`/`4:3`/`A1`）、`theme`（15 选 1）、`max_bullets`（默认 6）、`name/title/author`（元数据）、`dry_run`（只预检不产出）。

状态码：`200` 成功 · `400` 请求/文稿不可用 · `422` 版式校验失败（溢出/裸文本等，逐条原因） · `500` 结构校验失败（告警级） · `504` 转换超时。

---

## 5. 改版式（不需要改 Python）

编辑 `app/templates/<主题>/*.css`；`{{WIDTH}}`、`{{HEIGHT}}`、`{{CONTENT_WIDTH}}`、`{{WRAP_WIDTH}}`、`{{SUB_WIDTH}}` 渲染时替换为画布尺寸。硬约束（转换器强制，违反即 422）：`body` 精确像素且内容溢出 >1px 失败；文字必须在 `p/h1-h6/ul/ol` 内；各页尺寸一致；文本底部留 ≥0.5 英寸；图片本地存在。**排版问题会明确失败，不会静默产出破损文件。**

---

## 6. 配置

运行配置全部来自环境变量（`.env` / compose 注入）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PPTSVC_OUTPUT_DIR` | `<服务目录>/output` | 生成物根目录 |
| `PPTSVC_CONVERTER` | `vendor/html2pptx/html2pptx_cli.js` | 转换器入口 |
| `PPTSVC_TEMPLATES_DIR` | `app/templates` | 主题目录 |
| `PPTSVC_NODE` | `node` | node 可执行文件 |
| `PPTSVC_NODE_PATH` | 自动探测 | 传给转换子进程的 `NODE_PATH` |
| `PPTSVC_MAX_CONCURRENCY` | `2` | 并发转换数（每实例一个 Chromium ≈300–500MB） |
| `PPTSVC_TIMEOUT_SECONDS` | `300` | 单次转换超时 |
| `PPTSVC_KEEP_HTML` | `1` | 保留中间 HTML |
| `PPTSVC_PORT` | compose 内 `19000` | 服务端口 |
| `PLAYWRIGHT_BROWSERS_PATH` | 默认路径 | Chromium 位置（宿主跑时指 `.test-runtime/.pw-browsers`） |
| `BASE_IMAGE` / `DEBIAN_MIRROR` / `PLAYWRIGHT_DOWNLOAD_HOST`（构建参数） | 国内镜像源 | 换到正常网络时改回官方（`node:20-bookworm-slim` / `deb.debian.org` / `cdn.playwright.dev`） |

**宿主测试配置**集中在 `test.env`，重二进制位于仓库内 `.test-runtime/`（已 gitignore，不入库、不进 Docker 构建上下文）：

```ini
PLAYWRIGHT_BROWSERS_PATH=.test-runtime/.pw-browsers
PROBE_PLAYWRIGHT=.test-runtime/node_modules/playwright
PPTSVC_E2E=1
```

```powershell
& .\tools\run-tests.ps1              # 单元 + e2e 一键
& .\tools\run-tests.ps1 -UnitsOnly   # 只单元
node .probe\imgmodal.js              # 浏览器探针（读 PROBE_PLAYWRIGHT，未设则回退 .test-runtime）
```

验证层次（全部零 LLM）：L1 转换器严格校验（422）→ L2 结构校验（500）→ L3 幂等（两次构建 XML 逐字节比对）→ L4 视觉基线（可选）。

---

## 7. 生产注意事项

- **并发按内存调**：瓶颈在内存不在 CPU（实测 6 页约 1.1 秒）；
- **中文字体是镜像一部分**（`fonts-noto-cjk`）：换基础镜像必须重装，否则度量变化会触发溢出；
- **`output/` 持续增长**：定期清理历史 `task_id` 或改 TTL 策略；
- **不要编辑 `vendor/html2pptx/`**：升级 = 从上游钉版重新 vendor（保持可审查）。

---

## 8. 质量模型

| 层 | 手段 | 拦什么 |
|---|---|---|
| L1 契约 | 转换器 `--validate` 严格模式 | 溢出、裸文本、尺寸不一致 → 422 |
| L2 结构 | `verify.py` | 页数/画布错、文本被图片化、文案丢失、纯图页 → 500 |
| L3 幂等 | 同输入两次构建 XML 比对 | 非确定性回归 |
| L4 视觉 | LibreOffice 转 PNG 基线（可选） | 版式漂移 |

---

## 9. 输出目录

```
output/<task_id>/
├── request.json / manuscript.md    本次输入（自描述、可审计复现）
├── slides/slide01.html…            中间产物（排查/二次编辑）
├── answer.pptx                     交付物
└── result.json                     结果与校验报告
```

---

## 10. 开源引用与来源声明

本项目在下列开源组件之上构建，特此声明来源与许可证：

### 10.1 代码级引入（vendored，随仓库分发）

| 组件 | 来源 | 许可证 | 引入位置与用途 |
|---|---|---|---|
| **html2pptx 转换器**（HTML→PPTX 核心：Playwright 度量 + pptxgenjs 生成 + 严格校验） | [PPTAgent](https://github.com/icip-cas/PPTAgent) 项目 `deeppresenter/html2pptx/`，**钉版 v1.1.38**（与 v1.1.37 字节一致） | **MIT** | `vendor/html2pptx/`（4 个文件，见其 `PROVENANCE.md`）。**本项目唯一 vendored 上游代码**，其余 `app/` 均为原创；按「勿改、升级重新钉版」策略维护 |
| playwright（Node） | [microsoft/playwright](https://github.com/microsoft/playwright)，经上游依赖树引入 | Apache-2.0 | 转换器内浏览器度量与验证 |
| pptxgenjs | [gitbrent/PptxGenJS](https://github.com/gitbrent/PptxGenJS) | MIT | 转换器内 PPTX 生成 |
| sharp | [lovell/sharp](https://github.com/lovell/sharp) | Apache-2.0 | 转换器内图片处理 |
| fast-glob / minimist | npm 上游 | MIT | 转换器 CLI 工具 |

### 10.2 运行时依赖（requirements.txt / pip 安装）

| 组件 | 来源 | 许可证 | 用途 |
|---|---|---|---|
| FastAPI（含 Starlette、Pydantic） | [fastapi/fastapi](https://github.com/fastapi/fastapi) | MIT | HTTP 服务与数据校验 |
| uvicorn | [encode/uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI 服务器 |
| Pillow | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | HPND（MIT-CMU） | 原创代码使用：背景烘焙压暗、多宫格碎片预裁 |
| python-multipart | andrew-d/python-multipart | Apache-2.0 | 表单文件上传 |

### 10.3 镜像内系统组件

| 组件 | 来源 | 许可证 | 用途 |
|---|---|---|---|
| Noto Sans CJK SC 字体 | Google Noto（Debian `fonts-noto-cjk` 包） | SIL OFL 1.1 | 中文渲染度量 |
| Node.js 20 / Chromium | 官方基础镜像 + Playwright 下载 | Node/OpenSSL/BSD 类各自许可 | 转换运行时 |

### 10.4 设计情报（非代码引用）

- 生成主题的**行业色板**提炼自 ui-ux-pro-max 设计情报（科技/金融/教育/制造能源/政企/医疗六行业），经 `tools/build_themes.py` 离线生成并通过 WCAG AA 对比度审计后固化为主题 CSS——运行时不依赖任何外部服务。

**边界说明**：`app/`（渲染器、管线、校验、表单）、`tools/`、`tests/`、`.probe/` 为本项目原创代码；上游代码只存在于 `vendor/html2pptx/` 且保持钉版未改。
