# OPECT 文献雷达

面向 **OPECT（有机光电化学晶体管）** 的实时文献追踪站点。自动从 OpenAlex、Crossref、PubMed 抓取新论文，按相关性打分、去重、打标签，并提供检索筛选、时间线、统计分析与收藏笔记。

> 设计目标：不只是「把论文列出来」，而是把 OPECT 相关的**器件架构、光栅/沟道材料、异质结、生物传感、光电突触与神经形态计算**等工作一起收进来，并按与 OPECT 的相关程度排序。

---

## 1. 快速开始（Windows）

```powershell
cd opect-tracker
.\start.ps1
# 打开 http://127.0.0.1:8765
```

或双击 `start.bat`。

首次启动会自动做一次全量同步（约 2–5 分钟，取决于网络）；之后每 6 小时增量同步一次。右上角「同步」按钮可随时手动抓取，页面会实时显示抓取进度。

**环境要求**：Python 3.10+，无第三方依赖（全部使用标准库）。

```bash
# macOS / Linux
python3 -m opect.web --host 0.0.0.0 --port 8765
```

---

## 2. 功能

| 模块 | 说明 |
| --- | --- |
| **最新文献流** | 按发表时间倒序，卡片展示相关性分数、期刊、作者、主题标签、摘要、DOI/PDF 链接 |
| **相关性评分** | 10 组加权关键词 × 标题/摘要/关键词/期刊位置，加「光电 + 晶体管」交叉校验，输出 0–100 分与命中解释 |
| **检索筛选** | 全文检索（FTS5）+ 年份、主题、期刊、相关性层级、数据来源、收藏、作者多维筛选与排序 |
| **时间线** | 按月份倒序分组，快速看某一阶段的研究密度 |
| **统计分析** | 24 个月发表趋势、年度分布、主题构成、高频期刊、高产作者、来源覆盖 |
| **收藏与笔记** | 星标收藏、每篇文献的本地阅读笔记 |
| **导出** | CSV / BibTeX / JSON 一键导出，可直接导入 Zotero、EndNote |
| **自动同步** | 后台定时增量抓取（默认 6 小时），启动时自动补抓；支持关闭 |
| **多源合并** | 同一 DOI/标题跨源合并，保留更完整的摘要、作者、OA 链接 |

---

## 3. 目录结构

```
opect-tracker/
├─ config.json            # 站点、同步、关键词、评分权重、主题标签
├─ start.ps1 / start.bat  # Windows 启动脚本
├─ .github/workflows/
│  └─ publish.yml         # GitHub Actions：每天抓取并发布静态站
├─ deploy/
│  ├─ install.sh          # 服务器（systemd）安装脚本
│  ├─ update.sh           # 服务器更新脚本
│  └─ publish_github.py   # 一键创建仓库、上传代码、开启 Pages
├─ data/
│  └─ opect.db            # SQLite 数据库（首次运行自动创建）
├─ opect/
│  ├─ web.py              # HTTP 服务、路由、API、导出
│  ├─ static_site.py      # 静态站生成器（GitHub Pages 用）
│  ├─ views.py            # 页面渲染
│  ├─ templates.py        # HTML 组件、SVG 图表
│  ├─ sync.py             # 同步引擎、增量窗口、自动调度
│  ├─ sources.py          # OpenAlex / Crossref / PubMed / arXiv 适配器
│  ├─ scoring.py          # 相关性评分与主题标注
│  ├─ db.py               # SQLite 模式、去重合并、查询
│  └─ sync_once.py        # 命令行单次同步
└─ static/                # CSS / JS / 图标
```

---

## 4. 数据来源与检索策略

| 来源 | 用途 | 说明 |
| --- | --- | --- |
| **OpenAlex** | 主来源 | 覆盖最全、含摘要倒排索引、开放获取链接；按 `title_and_abstract.search` 精确检索 |
| **Crossref** | 补充 | 补抓出版商最新上线、DOI 元数据；对预印本（SSRN 等）覆盖好 |
| **PubMed** | 生物传感方向 | 补充 Analytical Chemistry、Biosensors 等生物分析类文献 |
| **arXiv** | 可选 | 默认关闭，OPECT 方向预印本较少，需要时在设置中开启 |

检索词在 `config.json → queries` 中维护，默认包括：

- `"photoelectrochemical transistor"`、`"opect"`、`"organic photoelectrochemical transistor"`
- `"photoelectrochemical" AND "organic electrochemical transistor"`
- `"photoresponsive" AND "organic electrochemical transistor"`
- `"photoactive gate"`、`"photogate"`
- `"photoelectrochemical" AND "PEDOT:PSS"`、`"photoelectrochemical" AND "transconductance"`

**增量策略**：以最近一次成功同步时间为起点回溯 45 天（`overlap_days`），避免出版商延迟索引造成漏检；首次同步回溯 10 年（`initial_lookback_days`）。

---

## 5. 相关性评分怎么算

评分逻辑在 `opect/scoring.py`，完全可解释：

1. **关键词组加权**：OPECT 核心词组 100 分，光敏栅/光电化学 30–42 分，OECT/OMIEC 18–26 分，生物传感/突触/异质结 10–14 分。
2. **位置加权**：标题权重 1.0，摘要 0.6×0.72，关键词 0.8，期刊名 0.35。
3. **覆盖加成**：命中 ≥2 个独立关键词组时最高 ×1.45，避免单一词重复刷分。
4. **交叉校验**：必须同时出现「光/光电」与「晶体管/OECT」语义，否则 ×0.45。
5. **核心保底**：明确出现 OPECT/光电化学晶体管时 +30 并保底 62 分。
6. **负向词**：`positron emission tomography`、`optical coherence tomography` 等缩写冲突词 ×0.35。

最终分数经 `100 × (1 − e^(−raw/175))` 压缩，落到 0–100。阈值：

- `≥ 70`：核心 OPECT（列表默认展示）
- `30–69`：强相关（列表默认展示）
- `18–29`：外围相关（仅入库，便于回溯）
- `< 18`：丢弃

> 修改 `config.json` 中的 `relevance.include_threshold` 可调整收录门槛；修改 `keywords` 可增删关键词组，重启后生效。

---

## 6. 配置说明（config.json）

```jsonc
{
  "site": { "title": "OPECT 文献雷达", "items_per_page": 20 },
  "sync": {
    "contact_email": "",          // 建议填写，进入 OpenAlex/Crossref 礼貌池
    "interval_minutes": 360,      // 自动同步间隔
    "on_start": true,             // 启动时若过期则抓取
    "initial_lookback_days": 3650,
    "overlap_days": 45,
    "min_year": 2015,
    "sources": { "openalex": true, "crossref": true, "pubmed": true, "arxiv": false }
  },
  "relevance": {
    "include_threshold": 30,
    "core_threshold": 70,
    "store_threshold": 18
  }
}
```

设置页可直接修改同步间隔、回溯天数、联系邮箱、数据源开关和相关性阈值，保存后立即生效。

---

## 7. 发布到 GitHub Pages（免费固定网址，电脑不用开机）

把网站导出为纯静态页面，交给 GitHub Actions 每天定时抓取并发布。地址形如
`https://<用户名>.github.io/opect-radar/`，**永久不变、自带 HTTPS、电脑关机也能访问**。

代价：静态站是只读的（没有「立即同步」按钮和云端笔记），收藏与笔记保存在浏览器本地；
数据每天自动更新一次。

### 7.1 一键发布（推荐）

1. 打开 https://github.com/settings/tokens → **Generate new token (classic)**
2. 勾选 `repo` 和 `workflow` 两个 scope，生成后复制
3. 在本机执行：

```powershell
$env:GITHUB_TOKEN = "ghp_你的token"
python deploy/publish_github.py --repo opect-radar
```

脚本会依次完成：创建仓库 → 上传 18 个文件 → 启用 Pages（来源选 GitHub Actions）→ 触发首次构建，
并打印最终网址。首次构建约 3–5 分钟（需要全量抓取一次文献）。

> 用完可以到 token 页面点 **Delete** 撤销，仓库和站点不受影响。

### 7.2 手动发布

1. 在 GitHub 新建一个 **Public** 仓库（例如 `opect-radar`）
2. 用网页 **Add file → Upload files** 上传 `opect/`、`static/`、`config.json`、`README.md`、`.gitignore`
3. 用 **Add file → Create new file**，文件名填 `.github/workflows/publish.yml`，粘贴仓库里同名文件的内容
4. 仓库 **Settings → Pages → Build and deployment → Source** 选 **GitHub Actions**
5. 打开 **Actions → Update OPECT radar → Run workflow**

### 7.3 本地预览静态站

```powershell
python -m opect.static_site --db data/opect.db --out dist/site
python -m http.server 8890 --directory dist/site
# 浏览器打开 http://127.0.0.1:8890/
```

仓库名不是 `<用户名>.github.io` 时，页面挂在子路径下，构建要加 `--base /仓库名`；
Actions 工作流已根据仓库名自动计算。

### 7.4 数据怎么保存

数据库不进仓库（避免仓库膨胀），由 Actions 缓存保存：每次运行先恢复上一次的
`data/opect.db`，抓取完再存回去。缓存丢失时下一次运行会自动全量重建，约 3 分钟。

---

## 8. 用公网链接访问本机（Windows 常驻，可选）

如果更想要完整功能（手动同步、云端笔记），可以让本机常驻并开一条隧道。

本机已有 `serve.ps1`：同时拉起网站和 Cloudflare Quick Tunnel，任一进程退出自动重启，并把当前公网地址写入文件。

```powershell
# 手动启动（前台运行，Ctrl+C 结束）
powershell -NoProfile -ExecutionPolicy Bypass -File serve.ps1

# 或者双击 start-public.bat
```

已注册两个计划任务，开机登录后自动运行、崩溃自动拉起：

| 任务名 | 作用 |
|---|---|
| `OPECT Tracker` | 登录时启动网站 + 隧道 |
| `OPECT Tracker Watchdog` | 每 5 分钟检查一次，进程掉了就补起来 |

```powershell
Start-ScheduledTask -TaskName "OPECT Tracker"    # 立即启动
Stop-ScheduledTask  -TaskName "OPECT Tracker"    # 停止
Get-Content data\public-url.txt                  # 查看当前公网地址
```

停止全部服务用 `stop.ps1`。

**地址在哪看**

- `data\public-url.txt`：当前公网地址（纯文本）
- `public-url.html`：双击自动跳转
- 桌面「OPECT 文献雷达」快捷方式：始终指向最新地址（隧道换地址后会自动更新）

**注意**

- Quick Tunnel 的域名是随机的，隧道进程重启后会变；桌面快捷方式会自动跟着变，但**收藏夹里的旧链接会失效**。
- 需要固定不变的地址，可选：① 用自己的域名 + Cloudflare 命名隧道；② 部署到 VPS；③ Tailscale Funnel。
- 电脑休眠会断掉隧道，已把「接通电源时」的睡眠关闭；笔记本用电池时仍会休眠。
- 该服务只对本机登录用户生效；注销/关机后需重新登录才会启动。

---

## 9. 部署到服务器

### 9.1 systemd（推荐）

```ini
# /etc/systemd/system/opect-radar.service
[Unit]
Description=OPECT Literature Radar
After=network-online.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/opect-tracker
Environment=PYTHONIOENCODING=utf-8
ExecStart=/usr/bin/python3 -m opect.web --host 127.0.0.1 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now opect-radar
```

### 9.2 Nginx 反向代理 + HTTPS

```nginx
server {
    listen 443 ssl http2;
    server_name opect.example.com;

    ssl_certificate     /etc/letsencrypt/live/opect.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/opect.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
```

### 9.3 Docker（可选）

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PYTHONIOENCODING=utf-8
EXPOSE 8765
CMD ["python", "-m", "opect.web", "--host", "0.0.0.0", "--port", "8765"]
```

```bash
docker build -t opect-radar .
docker run -d --name opect-radar -p 8765:8765 -v "$PWD/data:/app/data" opect-radar
```

> 公网部署提示：站点本身无登录鉴权。若需公开访问，建议在 Nginx 层加 Basic Auth 或接入统一认证；数据库为单文件 SQLite，备份 `data/opect.db` 即可。

---

## 10. 命令行

```bash
# 单次全量同步（忽略增量窗口）
python -m opect.sync_once --full

# 只抓某个来源
python -m opect.sync_once --source openalex --source crossref

# 启动服务（关闭自动同步）
python -m opect.web --no-auto --port 9000
```

---

## 11. HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sync/status` | 当前同步进度与统计 |
| POST | `/api/sync` | 触发同步 |
| GET | `/api/search?q=` | 检索建议（前 8 条） |
| POST | `/api/favorite` | `{"id": 1, "value": 1}` |
| POST | `/api/note` | `{"id": 1, "note": "..."}` |
| POST | `/api/rescore` | 按当前关键词与阈值重算全库相关性 |
| GET | `/export.csv` `/export.bib` `/export.json` | 导出 |
| GET | `/healthz` | 健康检查 |

---

## 12. 常见问题

**Q：同步很慢或失败？**
OpenAlex/Crossref 在部分网络环境下需要代理。设置 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量，或在 `sync.contact_email` 填入邮箱进入礼貌池（速率更高）。失败会自动重试 3 次，并在设置页的同步历史中记录。

**Q：想追更细的方向（如只追某一种材料）？**
在 `config.json → keywords` 中新增关键词组并调高权重，或在 `queries` 中增加一条检索式（如 `"BiVO4" AND "photoelectrochemical transistor"`），重启后生效。

**Q：数据会丢吗？**
SQLite 单文件存储在 `data/opect.db`，复制该文件即完成备份。重新同步不会重复入库（DOI/标题去重）。建议停服后复制，或用 `sqlite3 data/opect.db ".backup data/opect-backup.db"` 做在线备份。

**Q：中文能搜吗？**
可以。FTS5 默认分词器不切分中文，因此中文关键词走标题/摘要/主题标签的 `LIKE` 匹配，英文关键词走 FTS5 前缀索引，中英混合查询会同时应用两组条件。实测「异质结」「生物传感」「OPECT 生物传感」均能命中。

**Q：改了关键词，历史文献会重算吗？**
在「设置 → 重新计算相关性」点击一次即可按新配置重算全库，并清理低于收录线（默认 30 分）的条目；也可以用 `python -m opect.sync_once --rescore`。

**Q：为什么有些 2026 年的文章显示「即将发表」？**
部分出版商提前分配了卷期与在线日期，系统按 DOI 元数据中的日期展示；当日期晚于当天时统一显示为「即将发表」，避免出现「N 天后」这种不自然的表述。

**Q：能否接入 AI 中文摘要？**
可以。`works` 表已保留 `abstract` 字段，新增一个后台任务调用任意 LLM API 生成中文导读写入新字段即可；当前版本未内置，避免强制依赖外部 Key。
