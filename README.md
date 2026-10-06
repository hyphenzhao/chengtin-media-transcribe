# 澄听 · 本地多媒体工作台

内网地址：**http://192.168.50.94:3401/**

## 使用

1. 新建项目，选择或拖入音视频。刷新后重新选择同一文件，可继续未完成的分块上传。
2. 点击「开始转录」。灰框为正在积累的本地识别结果；配置并开启 AI 后，封段进入橙色修订队列，完成后可编辑。
3. 点击时间码回听；「原文与核对」保留两模型输出、截图文字、说话人时段和修改历史。
4. 处理完成后在「内容提取」选择纪要、摘要、面试记录或行动清单。提取完成自动生成「内容图谱」。图谱可缩放、全屏和导出 SVG/PNG。
5. 左下角配置兼容 Chat Completions 的 Base URL、模型和密钥，再在项目中选择该接口。未开启 AI 时，本地识别、OCR、说话人、笔记与导出均可使用。
6. 完成一次内容提取后，右上「内容问答」标签即可使用。选择项目 AI 接口，输入问题并按 Enter（Shift+Enter 换行）；支持连续追问、流式回答、停止与重试。问答随项目保存，点击回答下方时间码可回听。正文修改后，旧回答保留并显示版本提醒。

右上笔记和附件不加入 AI 请求。图片、视频、音频均在本机处理；只向用户选择的接口发送正文与对应画面 OCR 文字。系统不以知识库常识替换发言事实。模型分歧、画面冲突、多人混合发言仍需回听核对。

内容问答发送提问时的正文快照、提取结果及最近对话；不发送笔记或附件。长正文逐批检索相关原话，并检查引用文字确实存在、时间码对应真实段落。提取结果只作导航，原话无依据时应说明未找到。问答任务有独立队列，页面关闭后仍可完成。

内容提取按约 4,000 字、尽量靠近句末分段，逐段提取后再汇总；更长材料使用多级汇总。每段结果单独保存，同一任务重试会复用输入未变化的已完成段，旧的未完成提取也保留。单段输出受限时自动继续细分；整体汇总仍受限时，显示明确标注的完整分段版，不把截断内容当作完整纪要。

「AI 接口设置 → 生成长度与思考模式」可设置单次输出 token 上限与思考模式。上下文容量和单次输出预算是不同参数，思考模式还可能消耗生成预算。输出上限留空时由任务分配；自动模式对 DeepSeek 官方 `deepseek-flash` / `deepseek-v4-pro` 关闭思考，其他接口沿用模型默认。手动思考选项仅用于支持 `thinking` 参数的接口；启用思考且未指定上限时预留至少 32,768 tokens。任务记录保存结束原因与接口返回的用量，不保存思考原文、密钥或请求正文。

共享空间免登录，面向 `192.168.50.0/24`，另放行 VPN 网段 `10.8.0.0/24` 到 `192.168.50.94:3401`。手机经 VPN 访问时使用完整 HTTP 地址。桌面浏览器建议宽度不低于 1100px。三栏分隔线和右栏分隔线可拖动，布局保存在当前浏览器。

## 实现与原流程经验

| 部分 | 实现 |
|---|---|
| 界面 | React 19、TypeScript、Vite 6、Tailwind CSS 4、Tiptap 3、Mermaid 11；资源本地打包 |
| API / 存储 | Python 3.10、FastAPI、Pydantic、SQLAlchemy、SQLite WAL；原文件直接存本地磁盘 |
| 媒体 | FFmpeg 16kHz 单声道、高通与动态响度处理；浏览器代理保留原文件 |
| 转写 | GPU0 SenseVoice-Small；GPU1 faster-whisper large-v3；独立 Python 环境与进程 |
| 切分 | Silero VAD 辅助边界，最长约 28 秒，0.8 秒重叠；静音区间仍有固定窗口处理路径 |
| 视频 | 约每 5 秒和显著场景变化抓帧、去重、CPU RapidOCR / PP-OCRv5 |
| 说话人 | 两个独立 CPU 任务通道，sherpa-onnx segmentation-3.0 + 3D-Speaker ERes2Net；按首次出现编号，不阻塞下一段转写 |
| AI | httpx 流式兼容接口、长文分段压缩、来源时间校验、图表结构校验；不执行模型生成的代码 |
| 更新与恢复 | SQLite 持久事件 + SSE；原子分块提交、乐观版本检查、任务排队、取消和断点重试 |
| 进程 | 用户级 Supervisor、异常重启、日志轮转；用户 crontab 的幂等 `@reboot` 启动链路 |

旧面试流程的关键问题是远场低音量造成 VAD 漏掉大段内容、中英文和医学术语识别差异、提示词导致幻觉、投影姓名模糊。本应用沿用了响度处理、双模型和固定窗口覆盖，并将原始识别、截图来源和人工编辑版本一起保存。时间覆盖率表示处理了整段音频，不表示文字准确率。

远场多人录像仍可能把同一人分成多个匿名说话人组；编号不是已确认的实际人数。在转录设置中可选择较少碎片的分组方式，生成后可人工改名、调整段落归属。短片段仅有提问时，AI 必须标记未提供回答；接入模型的能力仍会影响修订与提取质量。

## 文件布局

应用根目录为 `/Volumes/Workspace/Servers/media-transcribe`。

- `backend/`：API、数据库、调度器、ASR/OCR/说话人和 AI 处理。
- `frontend/`：界面源码；`dist/` 为 API 提供的静态页面。
- `.venv/`、`.venv-whisper/`、`.venv-sensevoice/`：三个独立环境。由已验证的依赖版本复制或重建，不修改原面试环境。
- `data/app.sqlite3`：项目、片段、版本、队列与事件；`data/projects/`：原素材、播放代理、识别缓存、截图、图表导出。
- `models/`：OCR 与说话人模型；`manifest.json` 和 `asr-manifest.json` 记录来源及 SHA256。
- `logs/`：服务日志和验收记录。
- `~/.config/media-transcribe/secret.key`：权限 0600 的加密主密钥。API Key 加密保存，读取配置不返回密钥。

语音模型复用服务器已下载的只读文件：

```
/home/haifeng/asr_work/models/large-v3
/home/haifeng/.cache/modelscope/models/iic--SenseVoiceSmall/snapshots/master
/home/haifeng/.cache/modelscope/models/iic--punc_ct-transformer_cn-en-common-vocab471067-large/snapshots/master
```

可使用 `WHISPER_MODEL`、`SENSEVOICE_MODEL`、`PUNC_MODEL`、`WHISPER_PYTHON`、`SENSEVOICE_PYTHON` 覆盖路径。运行期启用模型离线模式。`requirements.lock.txt`、两个识别环境锁文件和 `frontend/package-lock.json` 固定依赖。

## 维护

以下操作均在服务器应用目录执行：

```bash
# 查看进程、正常启动或更新后重启
.venv/bin/supervisorctl -c scripts/supervisord.conf status
bash scripts/start.sh
.venv/bin/supervisorctl -c scripts/supervisord.conf restart api worker

# 构建页面
export PATH=/home/haifeng/.nvm/versions/node/v26.3.0/bin:$PATH
(cd frontend && npm ci && npm run build)

# 自动化测试（使用独立临时数据库，不修改现有项目）
.venv/bin/python -m pytest -q
```

新安装时分别使用 Python 3.10 创建三个环境，按三个锁文件安装依赖，再运行 `.venv/bin/python scripts/download_models.py`。FFmpeg、CUDA 驱动及上面的 ASR 模型路径必须就绪。模型首次下载需要网络，日常运行不需要模型下载。

`scripts/install_cron.py` 只替换带本应用标记的条目。启动器位于 `~/.local/lib/media-transcribe/boot.sh`，日志位于 `~/.local/state/media-transcribe/boot.log`；二者不依赖素材盘先挂载，启动器等待挂载后进入应用目录，并使用文件锁防止重复启动。

防火墙规则由 `sudo bash scripts/firewall.sh` 幂等设置：允许内网子网及上述 VPN 网段访问 TCP 3401。2026-10-06 从 UFW 日志确认手机 VPN 请求被原局域网规则拦截后，按用户要求增加了整个 `10.8.0.0/24` 网段的持久规则。部署没有重启整台服务器。

备份时保存 `data/`、模型清单和服务器私有加密密钥。数据库运行中使用 SQLite backup API 制作一致快照；直接复制时应先停止应用进程。恢复缺少加密密钥的数据库后，需要重新输入 AI Key。

## 验证

- `tests/test_pipeline.py`：分块上传、校验、Range、版本冲突、文本导出、AI 请求边界、来源验证、图结构、重试与持久化。
- `scripts/verify_local.py`：真实语音双模型、静音、无音轨视频 OCR、说话人、离线上传/播放/编辑/导出。可在 `unshare --user --map-root-user --net` 网络隔离环境运行。
- `scripts/verify_ai.py`：用服务器已有本地模型和明确标注的合成材料测试修订、流式纪要、图表全过程；不向外部发送数据。
- `tests/test_chat.py`：问答启用条件、请求去重、输入快照、隐私边界、连续追问、失败重试及原话引用。
- `tests/test_extraction.py`：输出上限与思考参数、流式末片段保留、无遗漏分段、断点复用、自适应拆分、汇总降级、来源修改后缓存失效和图表长度重试。
- `scripts/verify_chat.py`：隔离测试数据库，用本地模型在真实长录像正文中检索已知内容；不修改正式项目。
- `scripts/regression.py`：两段原始长录像的真实全程回归，原始文件保持不变。
- `logs/` 下的验收 JSON 保存实际执行结果。整机重启未纳入本次测试。

### 2026-10-03 部署验收记录

| 项目 | 实际结果 |
|---|---|
| 原始录像 IMG_4639.MOV | 71 分 19 秒；171 个识别块、42 个正文段落、250 条 OCR 证据；全时间覆盖、无重复块 |
| 原始录像 IMG_4640.MOV | 48 分 40 秒；121 个识别块、34 个正文段落、196 条 OCR 证据；全时间覆盖、无重复块 |
| 自动化检查 | 29 项通过，包含上传续传、版本冲突、来源时间、AI 中断、图表与导出边界 |
| 断网验证 | 独立 Linux 网络命名空间中真实调用双语音模型、OCR、说话人；验证上传、Range 播放、编辑、导出 |
| 恢复验证 | 只终止本应用工作进程，Supervisor 恢复后继续处理；最终无重复识别块 |
| AI 验证 | 真实 20 秒音频与单独标注的合成会议材料；使用服务器已有 qwen2.5:7b，经兼容接口完成修订、流式提取、图表；未发送到外部 API |
| 浏览器验证 | 真实上传、附件、笔记、自动保存和冲突处理、栏宽/高度拖动、布局记忆、图谱缩放及 SVG/PNG 文件验证 |
| 部署 | 内网 3401 可访问；UFW 仅允许 192.168.50.0/24；crontab 启动器安装并验证幂等启动 |

验收材料保留了双模型原始输出。远场低音量、医学术语、中英混合、多人声纹仍有明显识别误差；没有进行逐字人工标注，也未计算字错率。两份录像当前的 83/54 个匿名声纹组有过度拆分，不能视为真实人数，需人工调整归属。测试用小模型也出现过无依据补答、猜测时间和图结构不合规：已加强忠实提示、移除无依据时间引用、校验并重试结构；这些措施不能保证任意接入模型的内容准确性。

没有执行整机重启，也没有测试 20GB 上限文件的完整传输。临时测试项目放入可恢复的回收站，正式列表保留两份长录像。

同日追加内容问答后，自动化检查为 37 项通过。实际浏览器验证两轮真实音频问答、流式输出、时间码回听、刷新保留历史及笔记标签切换；使用本地模型在 20,857 字真实录像正文中找到了与问题相关的两段原话。详见 `logs/chat-verification.json` 与 `logs/chat-long-verification.json`。

### 2026-10-04 提取长度修复

真实 37 分钟录音的正文约 9,725 字，旧提取任务以 `max_tokens=10000` 请求 DeepSeek Flash，收到长度结束并留下 1,282 字未完成内容。旧实现没有记录思考用量，不能从该失败记录断言具体思考 token 数；1M 上下文不等于单次输出上限。

改用三段提取、再汇总，自动模式关闭该官方模型的思考输出。用同一正式项目、同一已配置接口完成真实重试，原失败结果保留。检查时间引用时发现模型可能忽略引用要求，因此增加逐项引用要求、分段来源锚点，以及未提供逐项引用时明确标注的原文索引。自动化检查增加到 48 项通过，前端生产构建通过。实际请求结束原因、用量、版本保留和最终图表检查保存于 `logs/extraction-verification.json`。

## 上游

[faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) · [RapidOCR](https://github.com/RapidAI/RapidOCR) · [sherpa-onnx 说话人文档](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html) · [Mermaid](https://mermaid.js.org/config/usage.html)
