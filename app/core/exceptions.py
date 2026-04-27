from __future__ import annotations


class MATPError(Exception):
    """平台基础异常，所有自定义异常继承此类。"""


class SearchError(MATPError):
    """搜索操作逻辑层面的失败（查询无效、结果为空等）。"""


class SearchBackendError(SearchError):
    """特定搜索后端失败（网络、HTTP、解析、限速）。
    auto 模式下抛出此异常时会自动尝试下一个 provider；
    指定 provider 时会向上传播。
    """


class LLMProviderError(MATPError):
    """LLM provider 在重试耗尽后仍失败。"""


class ToolError(MATPError):
    """工具执行失败（web_fetch、python_exec 等）。"""


class OrchestratorError(MATPError):
    """Orchestrator 层错误（图状态异常、节点失败等）。"""


class QueueError(MATPError):
    """任务队列操作错误。"""


class DatabaseError(MATPError):
    """数据库操作错误。"""
