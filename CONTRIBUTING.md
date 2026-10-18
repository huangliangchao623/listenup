# 参与贡献

## 环境搭建

见 [README 的快速开始](README.md#快速开始)。三个最容易踩的坑：

1. **`av` 必须锁 13.x** —— PyAV 14 移除了 `metadata_errors` 参数，
   faster-whisper 1.2.1 传了它，升级后识别结果会静默变成空
2. **模型不要提交** —— `tools/models/` 约 500 MB，用 `tools/fetch_model.py` 下载
3. **别提交 `data/`** —— 里面有账号、密码哈希、会话 token、你导入的媒体

## 提交前自检

```bash
python -m unittest discover -s app/tests    # 48 个测试，应全部通过
node --check app/web/app.js                  # 前端语法
```

改动切句算法的话，请同时跑：

```bash
python tools/bench_split.py    # 对比前后质量
```

## 代码风格

- Python：4 空格缩进，类型注解尽量写，函数写清楚"为什么这么做"而不是"做了什么"
- 前端：原生 JS，不引框架；CSS 用 `style.css` 里的设计令牌，不要硬编码颜色
- 注释写**决策理由**（为什么选 A 不选 B），这类信息比代码本身更难重建

## 提交信息

```
<范围>: <一句话说明>

<为什么改，改之前是什么问题>
```

例如：

```
sentence: 修复 and 开头的句子被并回上一句

_merge_conjunctions 原来只看当前句是否以连词开头，
没检查上一句是否已经以句末标点结束，导致两个独立句子被粘在一起。
```

## 不要做的事

- 不要提交任何形式的凭据（cookies、token、密码）
- 不要提交别人的课程内容或受版权保护的媒体
- 不要为"显示用户密码"这类需求加功能（不可逆哈希是刻意设计，见 SECURITY.md）
