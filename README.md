# JMComic PDF下载 · AstrBot 插件

AstrBot 插件，通过 QQ 命令搜索、查询、下载 JM 漫画，自动导出 PDF 并发送到当前会话。

## 功能一览

### 搜索
| 命令 | 说明 |
|------|------|
| `/jms <关键词>` | 搜索漫画，每页 5 个结果 |
| `/jms <关键词> -s <排序>` | 指定排序：`latest`/`views`/`pics`/`likes` |
| `/jms next` / `prev` | 翻页 |
| `/jms d <1-5>` | 下载当前页第 N 个结果 |
| `/jms info <1-5>` | 查看当前页第 N 个结果详情+封面 |

### 下载
| 命令 | 说明 |
|------|------|
| `/jm <ID>` | 下载漫画 PDF |
| `/jmcomic <ID>` | 同上 |

ID 支持：`422866` / `JM422866` / JM 完整链接

### 查询与管理
| 命令 | 说明 |
|------|------|
| `/jminfo <ID>` | 查看漫画详情+封面 |
| `/jmstatus` | 缓存状态 |
| `/jmclean` | 清理缓存（仅管理员） |
| `/jmhelp` | 帮助信息 |

## 配置

AstrBot Web 管理面板可调：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_pages` | 100 | 漫画页数上限，超过拒绝下载 |
| `max_file_size_mb` | 80 | PDF 文件大小上限 (MB) |
| `download_timeout` | 300 | 下载超时秒数 |
| `send_cover` | true | 查询时是否发送封面图 |
| `tag_limit` | 6 | 显示标签数上限 |
| `author_limit` | 3 | 显示作者数上限 |
| `sensitive_words` | (空) | 敏感词过滤，逗号分隔 |
| `auto_clean_file_amount` | 20 | 缓存会话数上限 |

## Docker 部署

在 AstrBot Web 面板设置：
```
callback_api_base: http://astrbot:6185
```

## 依赖

```bash
pip install jmcomic img2pdf pikepdf
```

## 许可证

MIT
