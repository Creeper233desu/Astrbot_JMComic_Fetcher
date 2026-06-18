"""
JMComic PDF下载插件

功能：
- /jmcomic <ID>  或  /jm <ID>  → 下载JM本子并导出为PDF发送到QQ
- /jmstatus → 查看下载缓存状态
- /jmclean → 清理所有下载缓存（仅管理员）

依赖：
- jmcomic >= 2.7.0
- img2pdf
- pikepdf (可选，PDF加密用)
"""

import asyncio
import os
import shutil
import traceback
import uuid
from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.message_components import Plain, File, Image
from astrbot.api import logger
from astrbot.api.event.filter import PermissionType
from astrbot.core import astrbot_config, file_token_service


class JmcomicPlugin(Star):
    """
    JMComic PDF下载插件

    通过QQ命令触发JM本子下载，自动导出为PDF并发送到当前会话。
    每次下载使用独立的工作目录，避免并发冲突。
    """

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

        # 插件数据目录：~/.astrbot/data/plugin_data/astrbot_plugin_jmcomic/
        self.data_dir = Path(StarTools.get_data_dir("astrbot_plugin_jmcomic"))

        # 下载缓存根目录
        self.cache_root = self.data_dir / "cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)

        # 并发锁：防止同一时间清理操作和下载操作冲突
        self._clean_lock = asyncio.Lock()

        # 搜索状态缓存（按用户sender_id隔离，每个用户独立翻页）
        self._search_states: dict[str, dict] = {}

        logger.info(f"JMComic插件已初始化，缓存目录: {self.cache_root}")

    # ======================== 命令处理 ========================
    @filter.command("jminfo")
    @filter.permission_type(PermissionType.MEMBER)
    async def cmd_info(self, event: AstrMessageEvent):
        """查看本子基本信息

        用法: /jminfo <本子ID>
        """
        msg = event.message_str.strip()
        for prefix in ["/jminfo ", "jminfo "]:
            if msg.startswith(prefix):
                msg = msg[len(prefix):].strip()
                break
        # 支持纯数字 / JM+数字 / URL
        jm_id = self._parse_jm_id(msg) if msg else None
        if not jm_id:
            yield event.plain_result("用法: /jminfo <本子ID>\n示例: /jminfo 422866")
            return

        yield event.plain_result(f"🔍 查询中: {jm_id}...")
        try:
            info_text, cover_path = await asyncio.to_thread(self._get_album_info, jm_id)
            if self.config.get("send_cover", True) and cover_path and os.path.exists(cover_path):
                yield event.chain_result([
                    Image.fromFileSystem(cover_path),
                    Plain(f"\n{info_text}"),
                ])
            else:
                yield event.plain_result(info_text)
        except Exception as e:
            yield event.plain_result(f"❌ 查询失败: {e}")

    @filter.command("jmhelp")
    @filter.permission_type(PermissionType.MEMBER)
    # 兼容 jmsearch 和 jmcomic jminfo的帮助命令
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        yield event.plain_result(
            "📖 JMComic 插件使用说明\n\n"
            "   搜索命令jms/jmsearch:\n"
            "  /jmsearch <关键词>              → 搜索（默认按最新排序）\n"
            "  /jmsearch <关键词> -s <排序>    → 指定排序方式\n"
            "  /jmsearch next                  → 下一页\n"
            "  /jmsearch prev                  → 上一页\n"
            "  /jmsearch d <序号>             → 下载当前页的某个结果\n\n"
            "   下载命令jmcomic/jms:\n"
            "  /jmcomic <本子ID或URL>          → 下载并发送PDF\n\n"
            "   查询命令jminfo:\n"
            "  /jminfo <本子ID>                → 查看本子基本信息\n\n"
            "   管理缓存(Admin Only):\n"
            "  /jmstatus                       → 查看缓存状态\n"
            "  /jmcache                        → 查看缓存文件列表\n"
            "  /jmclean                        → 清理所有缓存（管理员）\n\n"
            "   排序选项: latest(最新) views(最多观看) pics(最多图片) likes(最多喜欢)\n"
            "   示例: /jmsearch 全彩 -s views"
        )
    
    @filter.command("jmsearch", alias={"jms"})
    @filter.permission_type(PermissionType.MEMBER)
    async def cmd_search(self, event: AstrMessageEvent):
        """搜索JM本子

        用法:
          /jmsearch <关键词>              → 搜索（默认按最受欢迎排序）
          /jmsearch <关键词> -s <排序>    → 指定排序方式
          /jmsearch next                  → 下一页
          /jmsearch prev                  → 上一页
          /jmsearch d <序号>             → 下载当前页的某个结果

        排序选项: latest(最新) views(最多观看) pics(最多图片) likes(最多喜欢)
        序号: 1-5，对应当前显示的编号
        """
        msg = event.message_str.strip()
        sender_id = event.get_sender_id()

        # 去掉命令前缀 (兼容带/和不带/，AstrBot可能已剥离wake_prefix)
        for prefix in [
            "/jmsearch ", "/jms ",
            "jmsearch ", "jms ",
        ]:
            if msg.startswith(prefix):
                msg = msg[len(prefix):].strip()
                break

        # ====== 保留字优先匹配（必须在搜索之前判断） ======
        # next / prev（严格全匹配）
        if msg in ("next", "prev"):
            direction = 1 if msg == "next" else -1
            yield event.plain_result(await self._search_paginate(event, sender_id, direction))
            return

        # d <num>（下载选中结果）
        if msg.startswith("d "):
            async for result in self._search_download(event, msg):
                yield result
            return

        # info <序号>（查看当前搜索结果第N个的详情，1-5）
        if msg.startswith("info "):
            async for result in self._search_info(event, msg):
                yield result
            return

        # 帮助
        if not msg:
            yield event.plain_result(
                "🔍 **JMComic 搜索**\n\n"
                "用法:\n"
                "  /jms <关键词>               搜索\n"
                "  /jms <关键词> -s <排序>     指定排序\n"
                "  /jms next / prev            翻页\n"
                "  /jms d <1-5>                下载当前页结果\n\n"
                "排序: latest views pics likes\n"
                "示例: /jms 全彩 -s views"
            )
            return

        # ====== 搜索 ======
        sort_map = {"latest": "mr", "views": "mv", "pics": "mp", "likes": "tf"}
        order_by = "tf"  # 默认按最受欢迎排序
        query = msg
        if " -s " in msg:
            parts = msg.rsplit(" -s ", 1)
            query = parts[0].strip()
            order_by = sort_map.get(parts[1].strip().lower(), "tf")

        if not query:
            yield event.plain_result("请提供搜索关键词")
            return

        yield event.plain_result(f"🔍 搜索中: \"{query}\"...")
        try:
            result = await self._search_execute(sender_id, query, order_by)
            yield event.plain_result(result)
        except Exception as e:
            logger.error(f"[JMComic] 搜索异常: {e}\n{traceback.format_exc()}")
            yield event.plain_result(f"搜索失败: {e}")

    # ======================== 下载命令 ========================

    @filter.command("jmcomic", alias={"jm", "JM"})
    @filter.permission_type(PermissionType.MEMBER)
    async def cmd_download(self, event: AstrMessageEvent):
        """下载JM本子并发送PDF

        用法: /jmcomic <本子ID或URL>
        示例:
          /jm 422866
          /jmcomic JM422866
          /jm https://18comic.vip/album/422866/
        """
        # 解析命令参数
        jm_id = self._parse_jm_id(event.message_str)
        if not jm_id:
            yield event.plain_result(
                "📖 JMComic PDF下载\n\n"
                "用法: /jmcomic <本子ID>\n"
                "示例:\n"
                "  /jm 422866\n"
                "  /jmcomic JM422866\n"
                "  /jm https://18comic.vip/album/422866/\n\n"
                "支持输入: 纯数字ID / JM+数字 / JM链接"
            )
            return

        # 发送开始下载的提示
        yield event.plain_result(f"正在获取本子信息...\n ID: {jm_id}")

        # ---- 页数预检：超过上限直接拒绝，避免下载卡死 ----
        max_pages = self.config.get("max_pages", 100)
        page_count = await asyncio.to_thread(self._get_page_count, jm_id)
        if page_count is not None and page_count > max_pages:
            yield event.plain_result(
                f"这个本子过大了，别给我服务器干卡死了。拒绝下载\n"
                f" ID: {jm_id}\n"
                f"页数: {page_count}P (上限: {max_pages}P)\n"
                f"可修改 _conf_schema.json 中的 max_pages 调整上限"
            )
            return

        yield event.plain_result(f"页数: {page_count or '?'}P\n下载和PDF转换中，请稍候...")

        # ---- 诊断：打印 callback_api_base 的当前值 ----
        cb = astrbot_config.get("callback_api_base", "未设置")
        logger.info(f"[JMComic] callback_api_base = {cb}")
        if not cb or cb == "未设置":
            logger.warning(
                "[JMComic] callback_api_base 未配置！PDF文件将无法通过HTTP发送。"
                "请在 AstrBot Web面板 → 配置 中设置 callback_api_base = http://astrbot:6199，然后重启AstrBot。"
            )

        # 为本次下载创建独立的工作目录（避免并发冲突）
        session_id = uuid.uuid4().hex[:12]
        work_dir = self.cache_root / session_id
        pdf_dir = work_dir / "pdf"
        img_dir = work_dir / "images"

        try:
            # 在独立线程中执行阻塞的下载操作（带超时）
            timeout = self.config.get("download_timeout", 300)  # 默认5分钟
            result = await asyncio.wait_for(
                asyncio.to_thread(self._do_download, jm_id, str(img_dir), str(pdf_dir)),
                timeout=timeout,
            )

            if result is None:
                yield event.plain_result(f"❌ 下载失败: 无法获取本子 {jm_id}，请检查ID是否有效或网络连接")
                return

            album_name, pdf_path = result

            # 检查PDF文件
            if not pdf_path or not os.path.exists(pdf_path):
                yield event.plain_result(f"❌ PDF生成失败: 《{album_name}》\n")
                return

            file_size = os.path.getsize(pdf_path)
            file_size_mb = file_size / (1024 * 1024)

            # QQ文件大小限制检查
            max_size_mb = self.config.get("max_file_size_mb", 80)
            if file_size > max_size_mb * 1024 * 1024:
                yield event.plain_result(
                    f"这个本子过大了，别给我服务器干卡死了。拒绝下载\n"
                    f" ID: {jm_id}\n"
                    f"页数: {page_count}P (上限: {max_pages}P)\n"
                    f"可修改 _conf_schema.json 中的 max_pages 调整上限"
                )
                return

            callback_host = str(astrbot_config.get("callback_api_base", "")).rstrip("/")
            if not callback_host:
                yield event.plain_result("⚠️ callback_api_base 未设置，无法发送文件。请在AstrBot Web面板配置后重启。")
                return

            token = await file_token_service.register_file(pdf_path)
            file_url = f"{callback_host}/api/file/{token}"
            logger.info(f"[JMComic] 文件已注册: {file_url}")

            # 2. 尝试 OneBot 原生 upload_file API
            bot = getattr(event, "bot", None)
            if bot is not None:
                try:
                    gid = event.get_group_id()
                    if gid:
                        await bot.call_action("upload_group_file", group_id=int(gid), file=file_url, name=Path(pdf_path).name)
                    else:
                        await bot.call_action("upload_private_file", user_id=int(event.get_sender_id()), file=file_url, name=Path(pdf_path).name)
                    logger.info(f"[JMComic] upload_file API 发送成功")
                    send_ok = True
                except Exception as e:
                    logger.warning(f"[JMComic] upload_file API 失败({e})，回落 File 消息组件")
                    send_ok = False
            else:
                send_ok = False

            # 3. 回落方案: File 消息组件
            if not send_ok:
                yield event.chain_result([
                    File(name=Path(pdf_path).name, url=file_url),
                ])
                        # 发送完成提示
            yield event.plain_result(
                f"✅ 下载完成！\n"
                f"📖 《{album_name}》\n"
                f"JM车牌号: {jm_id}\n"
                f"请求人：{event.get_sender_name()} ({event.get_sender_id()})\n"
                #f"📄 {Path(pdf_path).name}\n"
                f"📦 {file_size_mb:.1f}MB\n"
                #f"📤 正在发送文件..."
            )

            logger.info(f"JMComic下载并发送完成: {jm_id} -> {album_name} ({file_size_mb:.1f}MB)")

        except asyncio.TimeoutError:
            timeout = self.config.get("download_timeout", 300)
            logger.warning(f"JMComic下载超时: {jm_id} (>{timeout}秒)")
            yield event.plain_result(
                f" 下载超时\n"
                f" ID: {jm_id}\n"
                f" 已超过 {timeout} 秒，任务已终止\n"
                f" 该本子可能过大，可调高 download_timeout 或降低 max_pages"
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"JMComic下载异常: {jm_id}\n{traceback.format_exc()}")
            yield event.plain_result(
                f"❌ 下载出错\n"
                f" ID: {jm_id}\n"
                f" 错误: {error_msg[:200]}\n\n"
                f"可能的原因:\n"
                f"• 本子ID不存在或已被删除\n"
                f"• 网络连接问题（可能需要配置代理）\n"
                f"• 本子图片无法访问\n"
                f"• 磁盘空间不足"
            )
        finally:
            # asyncio.create_task(self._delayed_cleanup(work_dir))
            # 自动清理过多的缓存文件
            await self._auto_clean_cache()
        
    @filter.command("jmstatus")
    @filter.permission_type(PermissionType.ADMIN)
    async def cmd_status(self, event: AstrMessageEvent):
        """查看下载缓存状态"""
        total_size = 0
        file_count = 0
        dir_count = 0

        if self.cache_root.exists():
            for f in self.cache_root.rglob("*"):
                if f.is_file():
                    total_size += f.stat().st_size
                    file_count += 1
            dir_count = len(list(self.cache_root.iterdir()))

        yield event.plain_result(
            f"📊 JMComic 缓存状态\n\n"
            f"📁 缓存目录: {self.cache_root}\n"
            f"   下载会话: {dir_count} 个\n"
            f"   缓存文件: {file_count} 个\n"
            f"💾 总大小: {total_size / (1024 * 1024):.1f}MB\n\n"
            f"   使用 /jmclean 清理所有缓存"
        )

    @filter.command("jmcache") 
    @filter.permission_type(PermissionType.ADMIN)
    #查看缓存的pdf名称，但不提供下载链接（因为文件可能已过期），
    #每页显示10个文件，每次查看显示下一页，每次查看都更新文件列表（因为可能有新的下载会话产生），
    #管理员和普通用户都可以使用
    async def cmd_view_cache(self, event: AstrMessageEvent):
        """查看缓存的PDF文件列表（分页显示）"""
        if not self.cache_root.exists():
            yield event.plain_result("缓存目录不存在，暂无缓存文件")
            return

        # 获取所有PDF文件
        pdf_files = sorted(
            self.cache_root.rglob("*.pdf"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if not pdf_files:
            yield event.plain_result("缓存目录中没有PDF文件")
            return

        # 分页显示，每页10个文件
        page_size = 10
        total_files = len(pdf_files)
        total_pages = (total_files + page_size - 1) // page_size

        # 获取当前页码（从消息中解析，默认为1）
        msg = event.message_str.strip()
        page_num = 1
        for prefix in ["/jmcache ", "jmcache ", "JMCACHE "]:
            if msg.startswith(prefix):
                try:
                    page_num = int(msg[len(prefix):].strip())
                    if page_num < 1:
                        page_num = 1
                except ValueError:
                    pass
                break

        start_index = (page_num - 1) * page_size
        end_index = start_index + page_size
        files_to_show = pdf_files[start_index:end_index]

        if not files_to_show:
            yield event.plain_result(f"没有更多PDF文件了（总共 {total_files} 个，当前页 {page_num}/{total_pages}）")
            return

        file_list_str = "\n".join(
            f"{i+1}. {f.name} ({f.stat().st_size / (1024 * 1024):.1f}MB)"
            for i, f in enumerate(files_to_show, start=start_index)
        )

        yield event.plain_result(
            f"📁 JMComic 缓存PDF文件列表\n\n"
            f"{file_list_str}\n\n"
            f"共 {total_files} 个PDF文件\n"
            f"当前页 {page_num}/{total_pages}\n"
            f"使用 /jmcache <页码> 查看下一页"
        )


    @filter.command("jmclean")
    @filter.permission_type(PermissionType.ADMIN)
    async def cmd_clean(self, event: AstrMessageEvent):
        """清理所有下载缓存（仅管理员）"""
        async with self._clean_lock:
            count = 0
            if self.cache_root.exists():
                for item in self.cache_root.iterdir():
                    try:
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink()
                        count += 1
                    except Exception as e:
                        logger.warning(f"清理缓存失败: {item} - {e}")

        yield event.plain_result(f"🧹 缓存已清理\n共清理 {count} 个下载会话")

    # ======================== 搜索核心逻辑 ========================

    def _get_jm_option(self):
        """构建 jmcomic Option（供搜索和下载共用）"""
        from jmcomic import create_option_by_str

        option_yaml = f"""
dir_rule:
  base_dir: {self.cache_root}
  rule: Bd_Aid_Pindex
download:
  cache: true
  image:
    decode: true
    suffix: .jpg
  threading:
    image: 30
client:
  impl: api
  postman:
    meta_data:
      proxies: null
"""
        return create_option_by_str(option_yaml)

    async def _search_execute(self, sender_id: str, query: str, order_by: str) -> str:
        """执行搜索并缓存结果，返回格式化后的第一页"""
        result = await asyncio.to_thread(self._do_search, query, order_by)
        if result is None:
            return "❌ 搜索无结果，请尝试其他关键词"

        search_page, total = result

        # 缓存搜索状态
        self._search_states[sender_id] = {
            "query": query,
            "order_by": order_by,
            "display_page": 0,          # 当前显示的5结果页(0-based)
            "server_pages": {0: search_page},  # 缓存已获取的服务器页
            "total": total,
        }

        return self._format_search_page(
            self._search_states[sender_id], search_page
        )

    def _do_search(self, query: str, order_by: str):
        """同步搜索方法（在线程中运行）"""
        option = self._get_jm_option()
        client = option.new_jm_client()

        page = client.search_site(
            search_query=query,
            page=1,
            order_by=order_by,
            time="a",       # TIME_ALL
            category="0",   # CATEGORY_ALL
        )

        if page.is_single_album:
            album = page.single_album
            content = [(album.album_id, {"id": album.album_id, "name": album.name, "author": album.author, "tags": album.tags})]
        else:
            # page.content 是 [(aid, info_dict), ...]，info_dict 含 name/author/tags
            # 不能用 list(page)，因为 __iter__ 返回的是 (aid, title字符串)
            content = list(page.content)

        total = page.total
        return (content, total)

    def _format_search_page(self, state: dict, content: list) -> str:
        """格式化搜索结果，每页5条"""
        display_page = state["display_page"]
        per_page = 5
        start = display_page * per_page
        end = start + per_page
        page_items = content[start:end]

        total = state["total"]
        total_pages = max(1, (total + per_page - 1) // per_page)
        current_page_display = display_page + 1

        sort_labels = {"mr": "最新", "mv": "最多观看", "mp": "最多图片", "tf": "最多喜欢"}
        sort_label = sort_labels.get(state.get("order_by", ""), state.get("order_by", "默认"))

        lines = [
            f"🔍 搜索: \"{state['query']}\"",
            f" 按{sort_label}排序 | 共{total}个结果 (第{current_page_display}/{total_pages}页)",
            f"━━━━━━━━━━━━━━━",
        ]

        for i, (aid, info) in enumerate(page_items, start=start + 1):
            title = info.get("name", "未知")[:30]
            author = info.get("author", "?")
            lines.append(f"{i}. [{aid}] {title}")
            lines.append(f"   作者: {author}")

        lines.append(f"━━━━━━━━━━━━━━━")
        lines.append(f"💡 /jmsearch(or jms) next → 下一页")
        lines.append(f"💡 /jmsearch(or jms) d <序号> → 下载")
        lines.append(f"💡 /jmsearch(or jms) info <序号> → 详情")

        return "\n".join(lines)

    async def _search_paginate(self, event, sender_id: str, direction: int) -> str:
        """翻页：direction=1下一页，-1上一页"""
        state = self._search_states.get(sender_id)
        if not state:
            return "⚠️ 请先使用 /jmsearch <关键词> 搜索"

        new_page = state["display_page"] + direction
        if new_page < 0:
            return "⚠️ 已经是第一页了"

        per_page = 5
        max_display_pages = max(1, (state["total"] + per_page - 1) // per_page)
        if new_page >= max_display_pages:
            return f"⚠️ 已经是最后一页了 (共{max_display_pages}页)"

        # 检查是否需要从服务器取下一页数据
        server_page_needed = (new_page * per_page) // 80  # 80=服务器每页条数
        if server_page_needed not in state["server_pages"]:
            try:
                content, _ = await asyncio.to_thread(
                    self._do_search_page,
                    state["query"],
                    state["order_by"],
                    server_page_needed + 1,  # 服务器页码从1开始
                )
                state["server_pages"][server_page_needed] = content
            except Exception as e:
                return f"❌ 获取第{new_page + 1}页失败: {e}"

        state["display_page"] = new_page

        # 从缓存中取出当前显示页的数据
        all_content = self._get_display_content(state)
        return self._format_search_page(state, all_content)

    def _do_search_page(self, query: str, order_by: str, server_page: int):
        """获取指定服务器页码的搜索结果"""
        option = self._get_jm_option()
        client = option.new_jm_client()

        page = client.search_site(
            search_query=query,
            page=server_page,
            order_by=order_by,
            time="a",  # TIME_ALL
            category="0",  # CATEGORY_ALL
        )

        if page.is_single_album:
            album = page.single_album
            return [(album.album_id, {"id": album.album_id, "name": album.name, "author": album.author, "tags": album.tags})]
        return list(page.content)  # 跟 _do_search 一样，用 .content 不用 __iter__

    def _get_display_content(self, state: dict) -> list:
        """从缓存中拼接出当前显示页需要的数据切片"""
        display_page = state["display_page"]
        per_page = 5
        start = display_page * per_page
        end = start + per_page

        # 从所有缓存的服务器页中收集数据
        all_items = []
        for sp in sorted(state["server_pages"].keys()):
            all_items.extend(state["server_pages"][sp])

        # 如果缓存不够（翻到了接近末尾的位置），可能需要截断
        return all_items

    async def _search_info(self, event, msg: str):
        """处理 /jms info <序号>：展示当前搜索结果第N个的详细信息"""
        sender_id = event.get_sender_id()
        state = self._search_states.get(sender_id)
        if not state:
            yield event.plain_result("⚠️ 请先使用 /jms <关键词> 搜索")
            return

        # 解析序号
        if msg.startswith("info "):
            num_str = msg[5:].strip()
        else:
            yield event.plain_result("⚠️ 用法: /jms info <1-5>")
            return

        if not num_str.isdigit():
            yield event.plain_result("⚠️ 请输入有效序号 (1-5)")
            return

        index = int(num_str) - 1
        if index < 0 or index >= 5:
            yield event.plain_result("⚠️ 序号范围 1-5")
            return

        display_page = state["display_page"]
        start = display_page * 5
        all_content = self._get_display_content(state)
        if index >= len(all_content[start:]):
            yield event.plain_result("⚠️ 该序号没有对应结果")
            return

        aid, _ = all_content[start + index]
        yield event.plain_result(f"🔍 查询中: {aid}...")
        try:
            info_text, cover_path = await asyncio.to_thread(self._get_album_info, aid)
            if self.config.get("send_cover", True) and cover_path and os.path.exists(cover_path):
                yield event.chain_result([
                    Image.fromFileSystem(cover_path),
                    Plain(f"\n{info_text}"),
                ])
            else:
                yield event.plain_result(info_text)
        except Exception as e:
            yield event.plain_result(f"❌ 查询失败: {e}")

    async def _search_download(self, event, msg: str):
        """处理 /jmsearch d <序号>（异步生成器，yield 消息给框架）"""
        sender_id = event.get_sender_id()
        state = self._search_states.get(sender_id)
        if not state:
            yield event.plain_result("⚠️ 请先使用 /jmsearch <关键词> 搜索")
            return

        # 解析序号: "d"
        if msg.startswith("d "):
            num_str = msg[2:].strip()  # 跳过 "d " (2个字符)
        else:
            yield event.plain_result("⚠️ 请输入有效序号，如: /jms d 3")
            return

        if not num_str.isdigit():
            yield event.plain_result("⚠️ 请输入有效序号，如: /jms d 3")
            return

        index = int(num_str) - 1  # 转为0-based
        if index < 0 or index >= 5:
            yield event.plain_result("⚠️ 序号范围 1-5")
            return

        # 从当前显示页获取结果
        display_page = state["display_page"]
        per_page = 5
        start = display_page * per_page

        all_content = self._get_display_content(state)
        if index >= len(all_content[start:]):
            yield event.plain_result("⚠️ 该序号没有对应结果")
            return

        item = all_content[start + index]
        aid, info = item
        title = info.get("name", "未知")

        # 触发下载
        yield event.plain_result(f"正在下载: [{aid}] {title}\n  请稍候...")

        # 复用下载逻辑
        try:
            async for result in self._do_search_result_download(event, str(aid)):
                yield result
        except asyncio.TimeoutError:
            timeout = self.config.get("download_timeout", 300)
            yield event.plain_result(f"下载超时 (>{timeout}秒)，任务已终止")
        except Exception as e:
            logger.error(f"[JMComic] 搜索下载失败: {e}")
            yield event.plain_result(f"❌ 下载失败: {e}")

    async def _do_search_result_download(self, event: AstrMessageEvent, jm_id: str):
        """下载搜索选中的本子"""
        # 页数预检
        max_pages = self.config.get("max_pages", 150)
        page_count = await asyncio.to_thread(self._get_page_count, jm_id)
        if page_count is not None and page_count > max_pages:
            yield event.plain_result(
                f"这个本子过大了，别给我服务器干卡死了。拒绝下载\n"
                f" ID: {jm_id}\n"
                f"页数: {page_count}P (上限: {max_pages}P)\n"
                f"可修改 _conf_schema.json 中的 max_pages 调整上限"
            )
            return

        session_id = uuid.uuid4().hex[:12]
        work_dir = self.cache_root / session_id
        pdf_dir = work_dir / "pdf"
        img_dir = work_dir / "images"

        try:
            timeout = self.config.get("download_timeout", 300)
            result = await asyncio.wait_for(
                asyncio.to_thread(self._do_download, jm_id, str(img_dir), str(pdf_dir)),
                timeout=timeout,
            )
            if result is None:
                yield event.plain_result(f"❌ 下载失败: 无法获取本子 {jm_id}")
                return

            album_name, pdf_path = result
            if not pdf_path or not os.path.exists(pdf_path):
                yield event.plain_result(f"❌ PDF生成失败: 《{album_name}》")
                return

            file_size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
            max_size_mb = self.config.get("max_file_size_mb", 80)
            if os.path.getsize(pdf_path) > max_size_mb * 1024 * 1024:
                yield event.plain_result(f"⚠️ 文件过大: 《{album_name}》 {file_size_mb:.1f}MB > {max_size_mb}MB")
                return

            # 文件服务注册 + 发送
            callback_host = str(astrbot_config.get("callback_api_base", "")).rstrip("/")
            if not callback_host:
                yield event.plain_result("⚠️ callback_api_base 未设置，无法发送文件")
                return

            token = await file_token_service.register_file(pdf_path)
            file_url = f"{callback_host}/api/file/{token}"

            bot = getattr(event, "bot", None)
            send_ok = False
            if bot:
                try:
                    gid = event.get_group_id()
                    if gid:
                        await bot.call_action("upload_group_file", group_id=int(gid), file=file_url, name=Path(pdf_path).name)
                    else:
                        await bot.call_action("upload_private_file", user_id=int(event.get_sender_id()), file=file_url, name=Path(pdf_path).name)
                    send_ok = True
                except Exception:
                    pass

            if not send_ok:
                yield event.chain_result([File(name=Path(pdf_path).name, url=file_url)])

            yield event.plain_result(
                f"✅ 下载完成！\n"
                f"📖 《{album_name}》\n"
                f"JM车牌号: {jm_id}\n"
                f"请求人：{event.get_sender_name()} ({event.get_sender_id()})\n"
                #f"📄 {Path(pdf_path).name}\n"
                f"📦 {file_size_mb:.1f}MB\n"
                #f"📤 正在发送文件..."
            )
        finally:
            await self._auto_clean_cache()

    # ======================== 下载核心逻辑 ========================

    def _get_jm_option_html(self):
        """构建 HTML 客户端 Option（HTML客户端能拿到 page_count）"""
        from jmcomic import create_option_by_str

        option_yaml = f"""
dir_rule:
  base_dir: {self.cache_root}
  rule: Bd_Aid_Pindex
client:
  impl: html
  postman:
    meta_data:
      proxies: null
"""
        return create_option_by_str(option_yaml)

    def _get_album_info(self, jm_id: str) -> tuple[str, Optional[str]]:
        """查询本子基本信息+下载封面（同步，在线程中运行）。
        返回 (文本信息, 封面路径或None)
        """
        option = self._get_jm_option_html()
        client = option.new_jm_client()
        album = client.get_album_detail(jm_id)

        tag_limit = self.config.get("tag_limit", 6)
        tag_limit = self.config.get("tag_limit", 6)
        tags = ", ".join(album.tags[:tag_limit] if tag_limit > 0 else []) if album.tags else "无"
        author_limit = self.config.get("author_limit", 3)
        authors = ", ".join(album.authors[:author_limit] if author_limit > 0 else []) if album.authors else "未知"
        photos = len(album)

        # 敏感词过滤
        tags = self._filter_sensitive(tags)
        authors = self._filter_sensitive(authors)

        text = (
            f"📖 《{album.name}》\n"
            f"━━━━━━━━━━━━━━\n"
            f" ID: {album.album_id}\n"
            f"作者: {authors}\n"
            f"页数: {album.page_count}P\n"
            f"章节: {photos} 章\n"
            f"标签: {tags}\n"
            f"❤️ {album.likes} | 👁 {album.views} | 💬 {album.comment_count}\n"
            f"━━━━━━━━━━━━━━"
        )

        # 下载封面
        cover_path = None
        try:
            cover_dir = self.cache_root / "covers"
            cover_dir.mkdir(parents=True, exist_ok=True)
            cover_path = str(cover_dir / f"{jm_id}.jpg")
            if not os.path.exists(cover_path):
                client.download_album_cover(jm_id, cover_path)
        except Exception as e:
            logger.warning(f"[JMComic] 封面下载失败: {jm_id} - {e}")

        return (text, cover_path if (cover_path and os.path.exists(cover_path)) else None)

    def _filter_sensitive(self, text: str) -> str:
        """过滤敏感词，替换为等长 * 号"""
        raw = self.config.get("sensitive_words", "")
        if not raw or not isinstance(raw, str) or not raw.strip():
            return text
        words = [w.strip() for w in raw.split(",") if w.strip()]
        for w in words:
            text = text.replace(w, "*" * len(w))
        return text

    def _get_page_count(self, jm_id: str) -> Optional[int]:
        """查询本子页数（同步，在线程中运行）。失败返回 None。"""
        try:
            option = self._get_jm_option_html()  # HTML客户端才能拿到真实page_count
            client = option.new_jm_client()
            album = client.get_album_detail(jm_id)
            return album.page_count
        except Exception as e:
            logger.warning(f"[JMComic] 查询页数失败: {jm_id} - {e}")
            return None

    def _parse_jm_id(self, message: str) -> Optional[str]:
        """从消息中解析JM本子ID

        支持格式:
        - /jm 422866 → "422866"
        - /jm JM422866 → "422866"
        - /jm https://18comic.vip/album/422866/ → 完整URL

        注意: AstrBot会自动剥离wake_prefix(如"/"), 所以命令前缀可能带/也可能不带
        """
        msg = message.strip()

        # 去掉命令前缀 (带/和不带/都兼容)
        for prefix in [
            "/jmcomic ", "/jm ", "/JM ",
            "jmcomic ", "jm ", "JM ",
        ]:
            if msg.startswith(prefix):
                msg = msg[len(prefix):].strip()
                break

        if not msg:
            return None

        # 取第一个空格前的部分作为ID
        jm_id = msg.split()[0] if msg else ""

        # URL格式直接返回（jmcomic可自动从URL解析ID）
        if "://" in jm_id:
            return jm_id

        # JM+数字格式：去掉JM前缀，提取纯数字
        jm_id = jm_id.strip().upper()
        if jm_id.startswith("JM") and len(jm_id) > 2 and jm_id[2:].isdigit():
            jm_id = jm_id[2:]

        # 纯数字校验
        if not jm_id.isdigit():
            return None

        return jm_id

    def _do_download(self, jm_id: str, img_dir: str, pdf_dir: str) -> Optional[tuple]:
        """执行下载操作（同步方法，在线程中运行）

        Args:
            jm_id: JM本子ID
            img_dir: 图片下载目录
            pdf_dir: PDF输出目录

        Returns:
            (album_name, pdf_path) 或 None
        """
        from jmcomic import download_album, Feature, create_option_by_str

        # 确保目录存在
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(pdf_dir, exist_ok=True)

        # 配置下载选项
        option_yaml = f"""
dir_rule:
  base_dir: {img_dir}
  rule: Bd_Aid_Pindex
download:
  cache: true
  image:
    decode: true
    suffix: .jpg
  threading:
    image: 30
client:
  impl: api
  postman:
    meta_data:
      proxies: null
"""

        option = create_option_by_str(option_yaml)

        # 下载本子并导出为PDF
        album, downloader = download_album(
            jm_id,
            option=option,
            extra=Feature.export_pdf(
                pdf_dir=pdf_dir,
                filename_rule="Atitle",  # 以本子标题命名PDF
                delete_original_file=False,
            ),
        )

        # 在PDF目录中查找生成的PDF文件
        pdf_files = sorted(
            Path(pdf_dir).glob("*.pdf"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        pdf_path = str(pdf_files[0]) if pdf_files else None

        if pdf_path:
            logger.info(f"PDF已生成: {pdf_path} ({os.path.getsize(pdf_path) / 1024 / 1024:.1f}MB)")

        return (album.name, pdf_path)  # type: ignore[union-attr]

    # ======================== 工具方法 ========================

    async def _auto_clean_cache(self):
        """自动清理过多的缓存文件，保留最新的N个会话"""
        max_cache = self.config.get("auto_clean_file_amount", 20)
        if not self.cache_root.exists():
            return

        sessions = sorted(
            [d for d in self.cache_root.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime
        )

        if len(sessions) > max_cache:
            to_delete = sessions[:-max_cache]
            for session in to_delete:
                try:
                    shutil.rmtree(session, ignore_errors=True)
                    logger.info(f"自动清理过期缓存: {session}")
                except Exception as e:
                    logger.warning(f"自动清理失败: {session} - {e}")

    # ======================== 生命周期 ========================

    async def terminate(self):
        """插件卸载时的清理"""
        logger.info("JMComic插件已卸载")
