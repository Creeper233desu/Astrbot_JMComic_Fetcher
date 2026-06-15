# JMComic PDF下载 · AstrBot插件

通过QQ命令下载JM漫画，自动导出为PDF并发送到当前会话。

## 命令

| 命令 | 说明 |
|------|------|
| `/jm <ID>` | 下载漫画PDF |
| `/jmcomic <ID>` | 同上（完整命令） |
| `/jmstatus` | 查看缓存状态 |
| `/jmclean` | 清理缓存（仅管理员） |

### ID格式支持

```
/jm 422866
/jm JM422866
/jm https://18comic.vip/album/422866/
```

## 依赖

需要安装 `jmcomic` 和 `img2pdf`：

```bash
pip install jmcomic img2pdf pikepdf
```

## 配置

在 AstrBot Web 管理面板可调整：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_file_size_mb` | 80 | PDF大小上限(MB) |

## 工作流程

```
QQ命令 → 解析JM ID → 下载图片 → img2pdf转PDF → 发送文件到QQ
```

每次下载使用独立临时目录，发送完成后自动清理。

## 许可证

MIT

##致谢
本项目基于/引用了以下开源项目：
-[JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python/blob/master/assets/docs/sources/option_file_syntax.md)
