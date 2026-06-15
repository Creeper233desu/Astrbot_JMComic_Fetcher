"""
JMComic PDF下载插件

功能：
- /jmcomic <ID>  或  /jm <ID>  → 下载JM漫画并导出为PDF发送到QQ
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
from astrbot.api.message_components import Plain, File
from astrbot.api import logger
from astrbot.api.event.filter import PermissionType


class JmcomicPlugin(Star):
    """
    JMComic PDF下载插件

    通过QQ命令触发JM漫画下载，自动导出为PDF并发送到当前会话。
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

        logger.info(f"JMComic插件已初始化，缓存目录: {self.cache_root}")

    # ======================== 命令处理 ========================

    @filter.command("jmcomic", alias={"jm", "JM"})
    @filter.permission_type(PermissionType.MEMBER)
    async def cmd_download(self, event: AstrMessageEvent):
        """下载JM漫画并发送PDF

        用法: /jmcomic <漫画ID或URL>
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
                "用法: /jmcomic <漫画ID>\n"
                "示例:\n"
                "  /jm 422866\n"
                "  /jmcomic JM422866\n"
                "  /jm https://18comic.vip/album/422866/\n\n"
                "支持输入: 纯数字ID / JM+数字 / JM链接"
            )
            return

        # 发送开始下载的提示
        yield event.plain_result(f"正在获取漫画信息...\n ID: {jm_id}\n下载和PDF转换中，请稍候...")

        # 为本次下载创建独立的工作目录（避免并发冲突）
        session_id = uuid.uuid4().hex[:12]
        work_dir = self.cache_root / session_id
        pdf_dir = work_dir / "pdf"
        img_dir = work_dir / "images"

        try:
            # 在独立线程中执行阻塞的下载操作
            result = await asyncio.to_thread(
                self._do_download, jm_id, str(img_dir), str(pdf_dir)
            )

            if result is None:
                yield event.plain_result(f"❌ 下载失败: 无法获取漫画 {jm_id}，请检查ID是否有效或网络连接")
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
                    f"⚠️ 文件过大，无法发送\n"
                    f"📖 漫画: 《{album_name}》\n"
                    f"📦 大小: {file_size_mb:.1f}MB (限制: {max_size_mb}MB)\n\n"
                    f"💡 建议: 该漫画页数较多，可尝试下载单章节"
                )
                return

            # 发送完成提示
            yield event.plain_result(
                f"✅ 下载完成！\n"
                f"📖 《{album_name}》\n"
                f"📄 {Path(pdf_path).name}\n"
                f"📦 {file_size_mb:.1f}MB\n"
                f"📤 正在发送文件..."
            )

            # 发送PDF文件
            yield event.chain_result([
                File(name=Path(pdf_path).name, file=str(pdf_path)),
            ])

            logger.info(f"JMComic下载成功: {jm_id} -> {album_name} ({file_size_mb:.1f}MB)")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"JMComic下载异常: {jm_id}\n{traceback.format_exc()}")
            yield event.plain_result(
                f"❌ 下载出错\n"
                f"📌 ID: {jm_id}\n"
                f"💥 错误: {error_msg[:200]}\n\n"
                f"可能的原因:\n"
                f"• 漫画ID不存在或已被删除\n"
                f"• 网络连接问题（可能需要配置代理）\n"
                f"• 漫画图片无法访问\n"
                f"• 磁盘空间不足"
            )
        finally:
            # 异步清理工作目录（延迟清理，确保文件已发送）
            asyncio.create_task(self._delayed_cleanup(work_dir))

    @filter.command("jmstatus")
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
            f"📂 下载会话: {dir_count} 个\n"
            f"📄 缓存文件: {file_count} 个\n"
            f"💾 总大小: {total_size / (1024 * 1024):.1f}MB\n\n"
            f"💡 使用 /jmclean 清理所有缓存"
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

    # ======================== 核心逻辑 ========================

    def _parse_jm_id(self, message: str) -> Optional[str]:
        """从消息中解析JM漫画ID

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
            jm_id: JM漫画ID
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

        # 下载漫画并导出为PDF
        album, downloader = download_album(
            jm_id,
            option=option,
            extra=Feature.export_pdf(
                pdf_dir=pdf_dir,
                filename_rule="Atitle",  # 以漫画标题命名PDF
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

    async def _delayed_cleanup(self, work_dir: Path, delay_seconds: int = 30):
        """延迟清理工作目录（等待文件发送完成）

        Args:
            work_dir: 要清理的工作目录
            delay_seconds: 延迟秒数
        """
        try:
            await asyncio.sleep(delay_seconds)
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
                logger.debug(f"工作目录已清理: {work_dir}")
        except Exception as e:
            logger.warning(f"清理工作目录失败: {e}")

    # ======================== 生命周期 ========================

    async def terminate(self):
        """插件卸载时的清理"""
        logger.info("JMComic插件已卸载")
