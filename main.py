#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键词触发插件 - 免指令前缀触发其他插件功能

功能说明：
1. 监听群聊所有消息
2. 当消息完全匹配配置的关键词时，自动添加指令前缀
3. 创建新事件派发到 AstrBot 的事件队列，让其他插件能够处理

使用示例：
- 用户发送 "打工" → 创建 "/打工" 命令事件 → 触发打工指令
- 用户发送 "宝宝菜单" → 创建 "/宝宝菜单" 命令事件 → 触发对应插件
"""

import contextvars
from astrbot.api import logger
from astrbot.api.star import Star, Context, register
from astrbot.api.event import filter, AstrMessageEvent

from .core.event_factory import EventFactory

# 防止无限递归的标记
_is_triggered_event = contextvars.ContextVar("is_triggered_event", default=False)


@register("keyword_trigger", "haoyue", "免指令前缀关键词触发插件", "1.0.0")
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

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        """获取群组ID"""
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'group_id'):
            return str(event.message_obj.group_id) if event.message_obj.group_id else ""
        return ""

    def _get_message_id(self, event: AstrMessageEvent) -> str:
        """获取消息ID"""
        if hasattr(event, 'message_obj') and hasattr(event.message_obj, 'message_id'):
            return str(event.message_obj.message_id) if event.message_obj.message_id else ""
        return ""

    @filter.event_message_type(filter.EventMessageType.ALL)  
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，匹配关键词后派发新的命令事件"""
        # 检查是否是我们自己触发的事件（通过 message_id 前缀判断）
        message_id = self._get_message_id(event)
        if message_id.startswith("command_trigger_"):
            logger.debug(f"[关键词触发] 跳过已触发的事件: {message_id}")
            return
        
        # 获取消息文本
        text = event.message_str.strip() if event.message_str else ""
        if not text:
            return
        
        # 获取原始消息（用于检查是否带前缀）
        raw_text = text
        if hasattr(event, 'message_obj'):
            msg_obj = event.message_obj
            # 尝试从 raw_message 获取原始文本
            if hasattr(msg_obj, 'raw_message'):
                if isinstance(msg_obj.raw_message, str):
                    raw_text = msg_obj.raw_message.strip()
                elif isinstance(msg_obj.raw_message, dict):
                    msg_content = msg_obj.raw_message.get('message', text)
                    # message 可能是 str 或 list
                    if isinstance(msg_content, str):
                        raw_text = msg_content.strip()
                    elif isinstance(msg_content, list) and msg_content:
                        # 尝试从 list 中提取文本
                        for item in msg_content:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                raw_text = item.get('data', {}).get('text', text).strip()
                                break
                            elif isinstance(item, str):
                                raw_text = item.strip()
                                break
            # 也尝试从 message 组件获取
            if hasattr(msg_obj, 'message') and msg_obj.message:
                from astrbot.api.message_components import Plain
                for comp in msg_obj.message:
                    if isinstance(comp, Plain) and hasattr(comp, 'text'):
                        raw_text = comp.text.strip()
                        break
        
        # 检查原始消息或当前消息是否已经是命令格式，跳过处理
        if (raw_text.startswith("/") or raw_text.startswith("#") or 
            text.startswith("/") or text.startswith("#")):
            logger.debug(f"[关键词触发] 跳过命令格式消息: raw='{raw_text}', text='{text}'")
            return
        
        # 检查是否仅限群聊
        if self.group_only:
            group_id = self._get_group_id(event)
            if not group_id:
                return
        
        # 检查是否匹配关键词（完全匹配）
        if text in self.keywords:
            new_command = f"/{text}"
            
            logger.info(f"[关键词触发] 匹配关键词 '{text}' → 转换为 '{new_command}'")
            
            try:
                # 获取发送者信息
                sender_id = str(event.get_sender_id())
                sender_name = event.get_sender_name() if hasattr(event, 'get_sender_name') else "用户"
                
                # 使用 EventFactory 创建新的命令事件
                new_event = self.event_factory.create_event(
                    unified_msg_origin=event.unified_msg_origin,
                    command=new_command,
                    creator_id=sender_id,
                    creator_name=sender_name
                )
                
                # 将新事件放入事件队列
                self.context.get_event_queue().put_nowait(new_event)
                
                logger.info(f"[关键词触发] 已派发命令事件: {new_command}")
                
                # 阻止原消息继续传播，避免 LLM 响应
                event.stop_event()
                
            except Exception as e:
                logger.error(f"[关键词触发] 派发事件失败: {e}")
