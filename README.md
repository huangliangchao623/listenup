# ListenUp · 英语听力训练

> 把任意视频/播客变成**可逐句精听**的听力材料。粘贴一个 B 站链接，或导入本地视频，
> 软件自动分离音轨、识别语音、按语义切成一句一句，点哪句听哪句，支持单句循环、
> 变速、隐藏字幕。带多用户账号体系，电脑开服务，手机扫码就能用。

一个**本地优先**的工具：所有音频处理、语音识别都跑在你自己电脑上，不依赖任何云服务。

---

## 为什么做这个

主流听力 App 的素材是它给你的；你想精听的那期播客、那个 TED、那门网课，通常不在里面。
而"把视频切成一句一句"这件事，手动做要几十个小时。

这个项目就是把这件事自动化：**给它一个视频，它给你一套精听材料。**

设计上有三个刻意的选择：

1. **识别在本机跑**（faster-whisper + CTranslate2），素材不出门，也没有按分钟计费
2. **切句不只看标点**，会结合词级时间戳的停顿长短；纯标点切分对口语素材几乎不可用
3. **服务常驻、多端共用**：电脑开着，手机、平板都能连；不是在每台设备上各跑一份

---

## 功能

**导入**
- B 站链接（优先用平台字幕，质量最好）
- 本地音视频文件
- YouTube / 抖音（依赖 yt-dlp，抖音需要配置 cookies）

**逐句精听**
- 点句子直接播放该句，句间可自动连播
- 单句循环、0.5×–2× 变速
- 隐藏字幕（先听再看）
- 整篇进度条 + 每句完成标记

**多用户**
- 账号密码登录，数据完全隔离（课程、进度、设置各归各的）
- 第一个注册的用户自动成为管理员
- 自定义头像、昵称、背景（5 套内置渐变 + 上传自己的图）

**桌面控制台**
- 服务状态、访问地址（含二维码）、用户管理、素材、实时日志、19 项环境自检

**平台**
- 浏览器（手机/电脑自适应）
- Windows 独立窗口（浏览器的应用模式）
- Android APK（WebView 客户端）

---

## 技术栈

| 层 | 选型 | 为什么 |
|---|---|---|
| 语音识别 | faster-whisper 1.2.1 + CTranslate2 | 同精度下比原版快 4 倍，CPU 上可用 |
| 音视频解码 | PyAV 13.1.0 | 直接调 FFmpeg 库，不用起子进程 |
| 切音频 | ffmpeg `atrim` + `asetpts` + 12ms 淡入淡出 | 精确到采样点，且没有爆音 |
| 后端 | FastAPI + Uvicorn | 异步、自带 OpenAPI 文档 |
| 数据库 | SQLite（WAL） | 单机多用户够用，零运维 |
| 前端 | 原生 JS + CSS | 无构建步骤，改完刷新就生效 |
| 离线 TTS | Piper (ONNX) | 备用朗读，不联网 |

---

## 快速开始

### 1. 环境

- Python **3.12**
- ffmpeg / ffprobe（放进 `tools/bin/`）

### 2. 装依赖

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r app/requirements.txt
```

> ⚠️ **`av` 必须锁在 13.x**。faster-whisper 1.2.1 调用
> `av.open(..., metadata_errors=...)`，而这个参数在 PyAV 14 被移除了。
> 一旦升级会直接抛 `TypeError`，表现是"能下载、能提取音轨，但识别结果永远是空"——
> 这个坑很隐蔽，因为整条链路都不报错，只是最后没有句子。

### 3. 准备模型与二进制

```bash
python tools/fetch_model.py      # 语音识别模型（约 500 MB）
python tools/fetch_bins.py       # ffmpeg / ffprobe
python tools/fetch_voice.py      # 离线朗读音色（可选）
```

### 4. 启动

```bash
python app/run.py --port 8765 --log
```

打开 <http://127.0.0.1:8765/>，第一次会引导你注册账号（第一个用户即管理员）。

---

## 它是怎么把语音切成"一句一句"的

这是整个项目最费劲的部分，也是踩坑最多的地方。

**朴素做法**：按标点切。对书面语勉强能用，对口语素材基本报废——播客里一句话讲
40 秒、中间夹七八个逗号的情况很常见，切出来全是碎片。

**本项目的做法**（`app/listenup/sentences.py`）：

```
词级时间戳
   ↓
① 句末强标点（. ? !）           → 直接断句
② 次标点（, ; : — 、）+ 停顿超过阈值 → 断成小句
③ 停顿超过阈值但没标点            → 也断（口语素材大量存在）
④ 单句超过时长/字符上限           → 强制切，优先在次标点处切
⑤ 碎片回收：太短又"未完成"的片段 → 并回上一句
```

几个必须处理的细节：

- **缩写不能断**：`Dr.` `U.S.` `a.m.` 和单个大写字母首字母（`J. K. Rowling`）
  后面跟着句点，但句子没结束
- **`and` 开头的不能并回上一句**：上一句已经有句末标点，说明是新的句子
- **强制切出来的碎片要标记**（`forced_cut`），否则会被当成正常句子
- **`natural_break` 标记**：合并短句时只并"未完成"的，不能把两个完整短句又粘回去

实测下来，真实播客里仍有约 30% 的碎片率。根本原因是**句尾停顿和句中停顿的时长
几乎一样**（实测句尾 0.72s，句中 0.70s），单靠停顿长短无法区分。要再提升需要
引入语义模型，这是后续方向。

辅助工具：

```bash
python tools/tune_split.py     # 在你的素材上扫描参数组合，自动评分推荐
python tools/bench_split.py    # 对比修改前后的切句质量
```

---

## 部署

### 局域网（手机连同一个 WiFi）

```bash
python app/run.py --port 8765
```

控制台会显示局域网地址，手机浏览器直接打开。也可以用 `http://listenup.local:8765`
（内置 mDNS 广播，不用记 IP）。

### 公网（手机在外面也能用）

家庭宽带通常没有公网 IP，路由器端口映射走不通，需要内网穿透：

```bash
python tools/natapp_tunnel.py <你的authtoken>
```

内置了**端口自动转发**：natapp 隧道的本地端口是在网页后台设置的，如果填的不是
8765（默认常是 80），脚本会在那个端口开一个 TCP 中继转到 8765，省得回去改配置。

长期运行用守护进程（挂了自动拉起）：

```bash
python tools/tunnel_watchdog.py
```

> 也支持 Cloudflare Tunnel，见 `tools/cf_named_tunnel.py`。国内访问延迟高 3–5 倍。

### Android APK

需要 JDK 17 + Android SDK（build-tools 34、platform 34）：

```bash
python tools/build_apk.py
```

不依赖 Gradle，直接调 aapt2 → javac → d8 → zipalign → apksigner。App 本身是一个
WebView 壳，首次打开让你填电脑的地址。

---

## 目录结构

```
app/
  listenup/           核心包
    config.py           路径与环境变量
    sources.py          平台链接识别与下载（yt-dlp）
    asr.py              语音识别 + 词级时间戳缓存
    sentences.py        ★ 切句算法
    pipeline.py         完整流水线：下载 → 识别 → 切句 → 切音频
    store.py            SQLite 数据层（含迁移）
    auth.py             账号、密码哈希、会话
    server.py           FastAPI 路由
    tts.py              朗读（Piper 离线 / edge-tts 在线）
    mdns.py             局域网名字广播
  web/                前端（无构建步骤）
  run.py              启动服务
  console.py          桌面控制台（tkinter）
  desktop.py          独立窗口客户端
  tests/              48 个单元测试

tools/                辅助脚本（取模型、构建 APK、隧道、调参）
android/              APK 源码（Manifest + Java + 资源）
```

---

## 已知限制

| 项 | 说明 |
|---|---|
| 切句准确率 | 真实播客约 70%，需要人工纠错的出口（**尚未实现**手动拆/并句） |
| 中文翻译 | 界面有开关，功能是空的 |
| 抖音 | 依赖 cookies，且无字幕只能靠识别，质量天生吃亏 |
| 并发导入 | 3 人同时导入就能打满 18 核，**尚未实现任务队列** |
| 多端登录 | 每个访问地址（IP / 域名）是独立的浏览器源，需要各登录一次 |
| 免费隧道 | natapp 免费版地址会不定时变更 |

---

## 路线图

- [ ] 手动拆分/合并句子
- [ ] 中文翻译（本地模型或 API）
- [ ] 单词查词 + 生词本
- [ ] 跟读录音对比
- [ ] 导入任务队列 + 每用户配额
- [ ] 邀请码注册
- [ ] 云同步（可选，默认关闭）

---

## 法律与使用边界

**本项目只是一个本地工具，不提供、不托管、不分发任何内容。**

- 请只处理你有权使用的内容，例如自己购买的课程、公开授权的播客、自己拍摄的视频
- **不要**用它下载有 DRM 保护的内容（Netflix / Disney+ 等），本项目也不支持
- 通过平台下载的内容，版权归原平台和创作者；**自行保存和传播可能构成侵权**
- 如果你把服务开放给他人使用，请务必了解：这可能从"个人使用"变成"提供工具/服务"，
  法律性质不同。建议限制为邀请制，并在用户协议中注明"仅限个人学习"

**软件按 MIT 协议"按原样"提供，作者不对使用后果承担责任。**

---

## 致谢

站在这些项目的肩膀上：

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [CTranslate2](https://github.com/OpenNMT/CTranslate2) — 语音识别
- [PyAV](https://github.com/PyAV-Org/PyAV) · [FFmpeg](https://ffmpeg.org/) — 音视频处理
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 平台下载
- [Piper](https://github.com/rhasspy/piper) — 离线语音合成
- [FastAPI](https://fastapi.tiangolo.com/) — Web 框架

## 许可证

[MIT](LICENSE)
