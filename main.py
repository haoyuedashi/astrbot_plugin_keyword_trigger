from astrbot.api import logger
from astrbot.api.star import Star, Context, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain, At

from .core.event_factory import EventFactory


@register("keyword_trigger", "haoyuedashi", "免指令前缀关键词触发插件", "1.0.0")
class KeywordTriggerPlugin(Star):
    """关键词触发插件 - 让用户无需输入前缀即可触发指令"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 解析关键词集合
        self.keywords = self._parse_keywords()
        
        # 是否仅在群聊中触发
        self.group_only = self.config.get("enable_group_only", True)
        
        # 创建事件工厂
        self.event_factory = EventFactory(context)
        
        # 记录已触发的事件 ID，防止重复处理
        self.triggered_event_ids = set()
        
        logger.info(f"[关键词触发] 插件已加载，共 {len(self.keywords)} 个关键词")
        if self.keywords:
            logger.info(f"[关键词触发] 关键词列表: {list(self.keywords)}")

    def _parse_keywords(self) -> set:
        """解析配置中的关键词列表，返回关键词集合"""
        keywords_config = self.config.get("keywords", [])
        result = set()
        
        for item in keywords_config:
            if isinstance(item, str) and item.strip():
                result.add(item.strip())
                    
        return result

    def _extract_non_text_components(self, event: AstrMessageEvent) -> list:
        """
        从原始消息中提取非文本组件（如 At 组件）
        这确保了真正的 @ 提及不会丢失
        """
        non_text_components = []
        
        try:
            if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message'):
                message_chain = event.message_obj.message
                if message_chain:
                    for comp in message_chain:
                        # 保留 At 组件（真正的艾特）
                        if isinstance(comp, At):
                            non_text_components.append(comp)
                            logger.debug(f"[关键词触发] 提取到 At 组件: qq={comp.qq}")
        except (AttributeError, TypeError) as e:
            logger.warning(f"[关键词触发] 提取消息组件时出错: {e}")
        
        return non_text_components

    def _is_valid_match(self, text: str, keyword: str) -> bool:
        """检查匹配是否有效（严格空格分隔）
        规则：
        1. 必须以关键词开头
        2. 如果 text 长度等于 keyword 长度，则是完全匹配，有效
        3. 如果 text 长度大于 keyword 长度，则 keyword 后的第一个字符必须是空格
        """
        if not text.startswith(keyword):
            return False
            
        # 完全匹配
        if len(text) == len(keyword):
            return True
            
        # 严格检查：关键词后面必须跟空格
        next_char = text[len(keyword)]
        if next_char != " ":
            return False
                
        return True

    @filter.event_message_type(filter.EventMessageType.ALL)  
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，匹配关键词后派发新的命令事件"""
        # 检查事件是否已经是指令（避免重复触发）
        if hasattr(event, 'is_at_or_wake_command') and event.is_at_or_wake_command:
            logger.debug(f"[关键词触发] 跳过已识别为指令的事件: {event.message_str}")
            return
        
        # 提取纯文本内容
        text = event.message_str.strip() if event.message_str else ""
        if not text:
            return
        
        # 跳过已经带命令前缀的消息
        if text.startswith("/") or text.startswith("#"):
            logger.debug(f"[关键词触发] 跳过命令格式消息: '{text}'")
            return
        
        # 检查是否仅限群聊
        if self.group_only:
            if not event.message_obj.group_id:
                return
        
        # 检查是否匹配关键词（前缀匹配，支持关键词后带参数）
        matched_keyword = None
        for keyword in self.keywords:
            if self._is_valid_match(text, keyword):
                # 优先选择更长的关键词匹配（避免短词误匹配）
                if matched_keyword is None or len(keyword) > len(matched_keyword):
                    matched_keyword = keyword
        
        if matched_keyword:
            # 获取关键词后面的内容（参数部分）
            suffix = text[len(matched_keyword):]
            new_command = f"/{matched_keyword}{suffix}"
            
            # 提取原始消息中的非文本组件（如 At 组件）
            original_components = self._extract_non_text_components(event)
            
            # 获取原始事件的管理员状态
            is_admin = False
            try:
                is_admin = event.is_admin()
            except (AttributeError, TypeError):
                pass
            
            logger.info(f"[关键词触发] 匹配关键词 '{matched_keyword}' → 转换为 '{new_command}'")
            if original_components:
                logger.info(f"[关键词触发] 保留 {len(original_components)} 个非文本组件")
            
            try:
                # 获取发送者信息
                sender_id = str(event.get_sender_id())
                sender_name = event.get_sender_name() if hasattr(event, 'get_sender_name') else "用户"
                
                # 使用 EventFactory 创建新的命令事件，传递原始消息组件和管理员状态
                new_event = self.event_factory.create_event(
                    unified_msg_origin=event.unified_msg_origin,
                    command=new_command,
                    creator_id=sender_id,
                    creator_name=sender_name,
                    original_components=original_components,
                    is_admin=is_admin
                )
                
                # 将新事件放入事件队列
                self.context.get_event_queue().put_nowait(new_event)
                
                logger.info(f"[关键词触发] 已派发命令事件: {new_command}")
                
                # 阻止原消息继续传播，避免 LLM 响应
                event.stop_event()
                
            except Exception as e:
                logger.error(f"[关键词触发] 派发事件失败: {e}")
