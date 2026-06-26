# JMComic Fetcher · AstrBot 插件

AstrBot 插件，通过 QQ 命令搜索、查询、下载 JM 漫画，自动导出 PDF 并发送到当前会话。支持 Docker 和 Windows 本地部署。

## 命令一览

### 🔍 搜索
| 命令 | 说明 |
|------|------|
| `/jms <关键词>` | 搜索漫画，每页 5 个结果 |
| `/jms <关键词> -s <排序>` | 指定排序 |
| `/jms next` / `prev` | 翻页 |
| `/jms d <1-5>` | 下载当前页第 N 个 |
| `/jms info <1-5>` | 查看当前页第 N 个详情+封面 |

排序选项：`latest` / `views` / `pics` / `likes`

### 📥 下载
| 命令 | 说明 |
|------|------|
| `/jm <ID>` | 下载漫画 PDF |
| `/jmcomic <ID>` | 同上 |

ID 格式：`422866` / `JM422866` / JM 完整链接

### 📋 查询
| 命令 | 说明 |
|------|------|
| `/jminfo <ID>` | 查看漫画详情+封面 |

### 📊 统计
| 命令 | 说明 |
|------|------|
| `/jmstat` | tag / author 下载次数排名 |
| `/jmstat next` / `prev` | 翻页 |

### 🗑️ 管理
| 命令 | 说明 |
|------|------|
| `/jmstatus` | 缓存状态 |
| `/jmclean` | 清理缓存（管理员） |
| `/jmhelp` | 帮助信息 |

## 配置项

AstrBot Web 管理面板可调：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_pages` | 100 | 漫画页数上限 |
| `max_file_size_mb` | 80 | PDF 大小上限 (MB) |
| `download_timeout` | 300 | 下载超时 (秒) |
| `download_threads` | 8 | 并发下载线程数 |
| `send_mode` | auto | 文件发送方式：auto/local/http |
| `access_mode` | none | 访问控制：none/whitelist/blacklist |
| `access_list` | (空) | 黑白名单列表 |
| `send_cover` | true | 是否发送封面图 |
| `enable_stats` | true | 是否启用下载统计 |
| `tag_limit` | 6 | 标签显示上限 |
| `author_limit` | 3 | 作者显示上限 |
| `sensitive_words` | (空) | 敏感词过滤 |
| `auto_clean_file_amount` | 20 | 缓存会话数上限 |

## Docker 部署

在 Web 面板配置：

```
send_mode: http
callback_api_base: http://astrbot:6185
```

`docker-compose.yml` 需映射端口 `6185` 和 `6199`。

## Windows 本地部署

无需额外配置，`send_mode` 保持默认 `auto` 即可。

## 依赖

```bash
pip install jmcomic img2pdf pikepdf
```

## 许可证

MIT
