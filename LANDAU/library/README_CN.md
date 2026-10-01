# LANDAU Library 模块

PhysMaster 的 arXiv 论文搜索与检索模块。

## 概述

Library 模块为 Theoretician、Supervisor 和 Critic agent 提供一个工具：

- **`library_search`** — 通过关键词、作者、标题、分类或 arXiv ID 搜索 arXiv 论文，返回标题、作者、摘要和 PDF 链接

## 实现

- **`arxiv_retriever.py`** — arXiv API v1 的 HTTP 客户端，纯标准库实现
- **`library_retrive.py`** — 封装层，提供 PhysMaster 其余模块所需的统一接口

使用 Python 标准库（`urllib`、`xml.etree.ElementTree`），无需额外依赖。

## 使用方式

Agent 在求解过程中可以调用该工具：

```python
# 按关键词搜索
library_search(query="quantum error correction surface code", top_k=5)

# 按字段前缀搜索
library_search(query="ti:black hole au:Hawking", top_k=3)

# 按 arXiv ID 查找单篇论文
library_search(query="2301.08727", top_k=1)
```

## arXiv API

- **接口地址：** `http://export.arxiv.org/api/query`
- **请求间隔：** 3 秒（由 `time.sleep(3)` 强制执行，arXiv 要求）
- **搜索语法：** 支持字段前缀 `ti:`（标题）、`au:`（作者）、`abs:`（摘要）、`cat:`（分类）
- **官方文档：** https://arxiv.org/help/api/

## 配置

在 `config.yaml` 中启用或禁用：

```yaml
landau:
  library_enabled: true
```

无需额外配置 — arXiv API 是公开的，不需要认证。如果初始化失败（如网络不通），系统会打印 Warning 并自动禁用该工具，pipeline 正常继续。
