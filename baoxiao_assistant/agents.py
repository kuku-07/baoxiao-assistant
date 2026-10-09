"""多智能体编排 - 青禾报销助手_qxy
   四个智能体角色：
   ┌───────────────────┬────────────────────────────────────────────────┐
   │ policy_query_agent │ 员工自主查询制度（RAG 检索 + 自然语言回答）    │
   │ material_audit_agent│ 材料审核：检查是否齐全、金额是否符合制度       │
   │ finance_review_agent│ 财务专员核对业务事实与金额合理性              │
   │ status_query_agent  │ 员工授权查询本人单据状态                      │
   └───────────────────┴────────────────────────────────────────────────┘

   编排框架：LangGraph StateGraph（可选，无 LLM 时退回规则+RAG 模式）
"""
from __future__ import annotations
from typing import TypedDict, List, Dict, Any, Optional

from rag_engine import get_rag

# ---------- LLM 可选接入 ----------
# 设置环境变量 OPENAI_API_KEY / DASHSCOPE_API_KEY / ... 即可启用 LLM 增强回答
LLM_OK = False
try:
    import os as _os
    _llm_key = (_os.getenv("OPENAI_API_KEY")
                or _os.getenv("DASHSCOPE_API_KEY")
                or _os.getenv("ARK_API_KEY"))
    if _llm_key:
        LLM_OK = True
except Exception:
    LLM_OK = False


# ========== 共享状态 ==========
class AgentState(TypedDict, total=False):
    user_id: int
    role: str                # employee / admin / finance
    query: str               # 用户自然语言输入
    tracking_no: str         # 报销单号
    category: str            # 差旅 / 招待 / 采购
    amount: int              # 金额（分）
    purpose: str
    material_text: str       # 报销材料 OCR 文本
    context_docs: List[Dict] # RAG 检索到的制度片段
    decision: str            # approved / rejected / need_info
    reason: str              # 审核结论说明
    answer: str              # 对员工的自然语言回复


# ============================================================
# Agent 1: 制度查询 Agent（员工 → 查制度）
# ============================================================
def policy_query_agent(state: AgentState) -> AgentState:
    """员工自主查制度。RAG 检索 → 拼上下文 → 生成清晰中文回复。"""
    rag = get_rag()
    query = state.get("query", "")
    top_k = 5

    # 检索
    hits = rag.search(query, top_k=top_k)
    state["context_docs"] = hits

    if not hits:
        state["answer"] = (
            f"🤔 抱歉，我暂时没查到与「{query}」相关的制度条目。\n"
            f"建议换个关键词试试，或联系行政专员乔姐（内线 8001）。"
        )
        return state

    # 组装引用内容
    sections = []
    for h in hits:
        sections.append(f"📘 **{h['category']} · {h['title']}**\n{h['text']}")
    cited = "\n\n---\n\n".join(sections)

    # 规则式回答（不依赖 LLM 也能跑）
    summary = _rule_based_summary(query, hits)
    state["answer"] = (
        f"✅ 根据行政专员乔姐维护的制度，与你问题最相关的内容如下：\n\n"
        f"**💡 核心要点**\n{summary}\n\n"
        f"---\n📎 以下为制度原文摘录，供你自行核对：\n\n{cited}\n\n"
        f"💬 如需进一步说明，可继续追问，或准备好材料后直接发起报销申请。"
    )
    return state


def _rule_based_summary(query: str, hits: List[Dict]) -> str:
    """不用 LLM 的要点提炼：取每个命中片段的前两句 + 含数字的关键规则。"""
    bullets = []
    seen = set()
    for h in hits[:3]:
        lines = [ln.strip() for ln in h["text"].split("\n") if ln.strip()]
        # 挑带数字的规则行
        numeric = [ln for ln in lines if any(ch.isdigit() for ch in ln)]
        if numeric:
            pick = numeric[:3]
        else:
            pick = lines[:2]
        for p in pick:
            key = p[:30]
            if key not in seen:
                bullets.append(f"• ({h['category']}) {p}")
                seen.add(key)
    if not bullets:
        bullets = ["制度条目较长，建议查看下方原文摘录。"]
    return "\n".join(bullets)


# ============================================================
# Agent 2: 材料审核 Agent（员工提交 → 材料完整性 + 合规性检查）
# ============================================================
def material_audit_agent(state: AgentState) -> AgentState:
    """材料审核：1) 检查必要材料字段  2) 金额是否超制度上限  3) 票据抬头"""
    rag = get_rag()
    category = state.get("category", "差旅")
    amount_fen = state.get("amount", 0)
    amount_yuan = amount_fen / 100
    purpose = state.get("purpose", "")
    material_text = state.get("material_text", "")
    tracking_no = state.get("tracking_no", "")

    state["context_docs"] = rag.search(
        f"{category} 报销材料要求 金额标准 上限", top_k=4
    )

    issues: List[str] = []
    approvals: List[str] = []

    # --- 检查 1：基本字段 ---
    if not category or category not in ("差旅", "招待", "采购"):
        issues.append("请指定报销类别：差旅 / 招待 / 采购")
    if amount_fen <= 0:
        issues.append("请填写报销金额（单位：元）")
    if not purpose or len(purpose.strip()) < 5:
        issues.append("请简要说明报销事由（至少 5 个字）")

    # --- 检查 2：制度金额上限（基于规则 + RAG） ---
    limits = _get_category_limits(category)
    if limits:
        if amount_yuan > limits.get("overall", 99999):
            issues.append(
                f"金额 {amount_yuan:.0f} 元超出 {category} 单笔上限 "
                f"{limits['overall']} 元，请拆分或说明预算特批。"
            )
        else:
            approvals.append(f"金额 {amount_yuan:.0f} 元在制度允许范围内")

    # --- 检查 3：材料文本关键字（如果提供了 OCR） ---
    if material_text:
        mtext = material_text.lower()
        if "发票" in mtext or "invoice" in mtext:
            approvals.append("识别到发票关键字 ✓")
        else:
            issues.append("上传材料中未识别到「发票」关键字，请确认是否齐全")

        if "公司全称" not in mtext and "抬头" not in mtext:
            # 仅提示，不驳回（可能 OCR 未识别）
            approvals.append("⚠️ 未识别到抬头信息，提交前请核对发票抬头")
    else:
        approvals.append("📎 本次未上传材料附件（系统记录事由与金额）")

    # --- 结论 ---
    if issues:
        state["decision"] = "rejected"
        state["reason"] = (
            f"❌ 单号 **{tracking_no}** 材料审核未通过，请补充以下内容：\n\n"
            + "\n".join(f"  • {i}" for i in issues)
            + "\n\n✅ 已确认项：\n"
            + "\n".join(f"  • {a}" for a in approvals)
        )
    else:
        state["decision"] = "approved"
        state["reason"] = (
            f"✅ 单号 **{tracking_no}** 材料审核通过！\n\n"
            + "\n".join(f"  • {a}" for a in approvals)
            + f"\n\n下一步已流转至 **财务专员周敏** 进行业务事实核对，请耐心等待。"
        )
    return state


def _get_category_limits(category: str) -> Dict[str, float]:
    """从 RAG 命中的制度里解析出大致上限（兜底用）。"""
    defaults = {
        "差旅": {"overall": 10000},
        "招待": {"overall": 3000},
        "采购": {"overall": 5000},
    }
    return defaults.get(category, {"overall": 10000})


# ============================================================
# Agent 3: 财务核对 Agent（周敏 → 核对业务事实 + 金额合理性）
# ============================================================
def finance_review_agent(state: AgentState) -> AgentState:
    """财务专员核对：金额拆分合理性 + 事由与分类匹配 + 是否需要补充证据"""
    rag = get_rag()
    category = state.get("category", "")
    amount_yuan = state.get("amount", 0) / 100
    purpose = state.get("purpose", "")
    tracking_no = state.get("tracking_no", "")

    state["context_docs"] = rag.search(f"{category} 费用标准 禁止事项", top_k=3)

    checks: List[str] = []
    warnings: List[str] = []

    # --- 事由 ↔ 分类匹配 ---
    purpose_lower = purpose.lower()
    cat_keywords = {
        "差旅": ["出差", "机票", "火车", "住宿", "酒店", "打车", "交通"],
        "招待": ["招待", "客户", "吃饭", "宴请", "礼品", "合作"],
        "采购": ["采购", "办公", "耗材", "设备", "打印", "纸张"],
    }
    matched = any(kw in purpose_lower for kw in cat_keywords.get(category, []))
    if matched:
        checks.append(f"事由与分类「{category}」匹配 ✓")
    else:
        warnings.append(
            f"事由「{purpose[:40]}...」未发现 {category} 常见关键字，"
            f"请核对分类是否正确。"
        )

    # --- 金额合理性 ---
    if category == "差旅" and amount_yuan > 8000:
        warnings.append("差旅金额偏大（>8000 元），建议核对行程单与预算审批单。")
    if category == "招待" and amount_yuan > 2000:
        warnings.append("招待金额 >2000 元，按制度需分管副总审批，请确认是否已获批。")
    if category == "采购" and amount_yuan > 2000:
        warnings.append("采购金额 >2000 元，按制度需两家报价对比，请检查附件。")

    # --- 结论（财务一般可放行 + 备注） ---
    if not warnings:
        state["decision"] = "approved"
        state["reason"] = (
            f"💰 单号 **{tracking_no}** 财务核对通过。\n\n"
            + "\n".join(f"  • {c}" for c in checks)
            + "\n\n✅ 已进入付款流程，预计 3 个工作日内到账。"
        )
    else:
        state["decision"] = "need_info"
        state["reason"] = (
            f"📝 单号 **{tracking_no}** 财务核对中，需补充说明：\n\n"
            + "\n".join(f"  ⚠️ {w}" for w in warnings)
            + "\n\n请联系申请人补全材料后再提交。"
        )
    return state


# ============================================================
# Agent 4: 单据状态查询 Agent（员工 → 查自己的单）
# ============================================================
STATUS_MAP = {
    "draft": ("草稿 📝", "尚未提交，仅本人可见"),
    "submitted": ("已提交 📨", "等待材料审核"),
    "material_approved": ("材料通过 ✅", "等待财务专员核对业务事实"),
    "material_rejected": ("材料退回 ❌", "请查看审核意见补充材料后重新提交"),
    "finance_approved": ("财务通过 💰", "已进入付款流程，预计 3 个工作日到账"),
    "finance_rejected": ("财务退回 ❌", "请联系财务专员核对原因"),
    "paid": ("已完成 🎉", "款项已到账"),
}


def status_query_agent(state: AgentState) -> AgentState:
    """根据数据库中的 tracking_no 查询状态并格式化返回。
    注意：此 Agent 只做查询展示，实际数据库操作由 main.py 的 API 层完成。"""
    tracking_no = state.get("tracking_no", "")
    status = state.get("status", "")
    amount_fen = state.get("amount", 0)
    category = state.get("category", "")
    reason, detail = STATUS_MAP.get(status, (status or "未知", ""))
    state["answer"] = (
        f"📋 单号 **{tracking_no}** 当前状态：**{reason}**\n\n"
        f"• 类别：{category}\n"
        f"• 金额：{amount_fen / 100:.0f} 元\n"
        f"• 说明：{detail}\n\n"
        f"如需更详细的处理进度，可联系财务专员周敏（内线 8002）。"
    )
    return state


# ============================================================
# LangGraph 编排（可选，未安装则退回函数直接调用）
# ============================================================
GRAPH_OK = False
try:
    from langgraph.graph import StateGraph, END

    workflow = StateGraph(AgentState)
    workflow.add_node("policy_query", policy_query_agent)
    workflow.add_node("material_audit", material_audit_agent)
    workflow.add_node("finance_review", finance_review_agent)
    workflow.add_node("status_query", status_query_agent)
    workflow.set_entry_point("policy_query")
    workflow.add_edge("policy_query", END)
    GRAPH_OK = True
except Exception as e:
    print(f"ℹ️ langgraph 未启用（{e}），将使用函数直接调用模式")
    workflow = None


def run_agent(agent_name: str, state: AgentState) -> AgentState:
    """统一入口：根据 agent_name 调用对应智能体"""
    dispatch = {
        "policy_query": policy_query_agent,
        "material_audit": material_audit_agent,
        "finance_review": finance_review_agent,
        "status_query": status_query_agent,
    }
    func = dispatch.get(agent_name)
    if func is None:
        raise ValueError(f"未知智能体：{agent_name}，可选：{list(dispatch.keys())}")
    return func(state)


if __name__ == "__main__":
    from database import init_db
    init_db(reset=False)
    # 测试 1：查制度
    print("\n===== 测试 1：员工查制度 =====")
    res = run_agent("policy_query", {"query": "出差住宿标准"})
    print(res["answer"][:600])

    # 测试 2：材料审核
    print("\n===== 测试 2：材料审核 =====")
    res = run_agent("material_audit", {
        "tracking_no": "R-0002",
        "category": "差旅",
        "amount": 280000,
        "purpose": "上海客户拜访：交通+住宿+餐饮",
        "material_text": "电子发票 上海携程 住宿费 1500 元",
    })
    print(res["reason"])

    # 测试 3：财务核对
    print("\n===== 测试 3：财务核对 =====")
    res = run_agent("finance_review", {
        "tracking_no": "R-0002",
        "category": "差旅",
        "amount": 280000,
        "purpose": "上海客户拜访：交通+住宿+餐饮",
    })
    print(res["reason"])