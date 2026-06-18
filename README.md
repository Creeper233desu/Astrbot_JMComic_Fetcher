# JMComic PDF下载 · AstrBot插件

通过QQ命令搜索、下载JM漫画，自动导出为PDF并发送到当前会话。

## 命令

### 搜索

| 命令 | 说明 |
|------|------|
| `/jmsearch <关键词>` | 搜索漫画（默认最新排序），每页5个结果 |
| `/jmsearch <关键词> -s <排序>` | 指定排序方式搜索 |
| `/jmsearch next` | 下一页 |
| `/jmsearch prev` | 上一页 |
| `/jmsearch d <1-5>` | 下载当前页对应序号的漫画 |

排序选项：`latest`（最新）、`views`（最多观看）、`pics`（最多图片）、`likes`（最多喜欢）

### 下载

| 命令 | 说明 |
|------|------|
| `/jm <ID>` | 下载漫画PDF |
| `/jmcomic <ID>` | 同上（完整命令） |

ID支持：纯数字 `422866`、`JM422866`、JM链接

### 管理

| 命令 | 说明 |
|------|------|
| `/jmstatus` | 查看缓存状态 |
| `/jmclean` | 清理缓存（仅管理员） |

## 配置

在 AstrBot Web 管理面板可调整：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_file_size_mb` | 80 | PDF大小上限(MB) |

## 依赖

```bash
pip install jmcomic img2pdf pikepdf
```

## Docker 部署

确保在 AstrBot Web 面板配置 `callback_api_base`：
```
http://astrbot:6185
```

## 许可证

MIT
