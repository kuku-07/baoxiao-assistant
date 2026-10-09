"""青禾报销助手_qxy - FastAPI 主应用
   启动：uvicorn main:app --reload --port 8000

   角色：
   ┌──────────┬──────────┬──────────────────────────────────┐
   │ 小林     │ 员工     │ 查制度 → 提交报销 → 查状态        │
   │ 乔姐     │ 行政专员 │ 维护制度文档（上传/删除/重建索引） │
   │ 周敏     │ 财务专员 │ 核对业务事实、审核报销单          │
   └──────────┴──────────┴──────────────────────────────────┘
"""
import os
import re
import secrets
from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from jinja2 import Environment, FileSystemLoader

from sqlalchemy.orm import Session
import models as M
from database import ENGINE, SessionLocal, init_db
from rag_engine import get_rag
from agents import run_agent, STATUS_MAP

# ========== 启动 ==========
init_db(reset=False)      # 首次运行会自动建表 + 写种子数据
M.Base.metadata.create_all(ENGINE)

app = FastAPI(title="青禾报销助手_qxy")

# 静态文件 & 模板
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

env = Environment(loader=FileSystemLoader("templates"), autoescape=True)

# Cookie 式简易 session（够 Demo 用）
_sessions: dict = {}
TOKEN_COOKIE = "baoxiao_token"

# ========== 依赖 ==========
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _current_user(request: Request, db: Session = Depends(get_db)) -> M.User:
    token = request.cookies.get(TOKEN_COOKIE)
    if not token or token not in _sessions:
        raise HTTPException(status_code=401, detail="请先登录")
    uid = _sessions[token]
    user = db.query(M.User).filter_by(id=uid).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


# ========== 前端页面 ==========
@app.get("/", response_class=HTMLResponse)
def login_page():
    tpl = env.get_template("login.html")
    return tpl.render()


@app.post("/login")
def login(username: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(M.User).filter_by(username=username).first()
    if not user:
        # Demo：不存在则自动创建（默认角色 employee）
        user = M.User(username=username, display_name=f"{username}",
                      role=M.UserRole.EMPLOYEE)
        db.add(user); db.commit(); db.refresh(user)
    token = secrets.token_hex(16)
    _sessions[token] = user.id
    role_home = {
        M.UserRole.EMPLOYEE: "/employee",
        M.UserRole.ADMIN:    "/admin",
        M.UserRole.FINANCE:  "/finance",
    }[user.role]
    resp = RedirectResponse(role_home, status_code=302)
    resp.set_cookie(TOKEN_COOKIE, token, httponly=True)
    return resp


@app.get("/logout")
def logout(request: Request):
    token = request.cookies.get(TOKEN_COOKIE, "")
    _sessions.pop(token, None)
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie(TOKEN_COOKIE)
    return resp


# ========== 员工端 ==========
@app.get("/employee", response_class=HTMLResponse)
def employee_home(user: M.User = Depends(_current_user),
                  db: Session = Depends(get_db)):
    if user.role != M.UserRole.EMPLOYEE:
        raise HTTPException(403, "无权限")
    tpl = env.get_template("employee.html")
    my_reimb = db.query(M.Reimbursement)\
        .filter_by(applicant_id=user.id)\
        .order_by(M.Reimbursement.created_at.desc())\
        .all()
    return tpl.render(user=user, reimbursements=my_reimb,
                      status_map=STATUS_MAP)


@app.get("/chat/policy", response_class=HTMLResponse)
def employee_chat(user: M.User = Depends(_current_user)):
    if user.role != M.UserRole.EMPLOYEE:
        raise HTTPException(403, "无权限")
    tpl = env.get_template("chat.html")
    return tpl.render(user=user, mode="policy", title="📖 制度查询助手")


@app.get("/reimburse/new", response_class=HTMLResponse)
def reimburse_form(user: M.User = Depends(_current_user)):
    if user.role != M.UserRole.EMPLOYEE:
        raise HTTPException(403, "无权限")
    tpl = env.get_template("reimburse_form.html")
    return tpl.render(user=user)


# ========== 行政端 ==========
@app.get("/admin", response_class=HTMLResponse)
def admin_home(user: M.User = Depends(_current_user),
               db: Session = Depends(get_db)):
    if user.role != M.UserRole.ADMIN:
        raise HTTPException(403, "无权限")
    tpl = env.get_template("admin.html")
    docs = db.query(M.PolicyDoc).order_by(M.PolicyDoc.updated_at.desc()).all()
    return tpl.render(user=user, docs=docs)


# ========== 财务端 ==========
@app.get("/finance", response_class=HTMLResponse)
def finance_home(user: M.User = Depends(_current_user),
                 db: Session = Depends(get_db)):
    if user.role != M.UserRole.FINANCE:
        raise HTTPException(403, "无权限")
    tpl = env.get_template("finance.html")
    pending = db.query(M.Reimbursement)\
        .filter(M.Reimbursement.status.in_(
            M.ReimbursementStatus.MATERIAL_APPROVED,
            M.ReimbursementStatus.FINANCE_APPROVED,
            M.ReimbursementStatus.FINANCE_REJECTED))\
        .order_by(M.Reimbursement.updated_at.desc()).all()
    all_list = db.query(M.Reimbursement)\
        .order_by(M.Reimbursement.created_at.desc()).all()
    return tpl.render(user=user, pending=pending, all_list=all_list,
                      status_map=STATUS_MAP)


# ============================================================
#              REST API（四个智能体 + 三个角色）
# ============================================================

# ---- 1) 员工：制度查询 Agent ----
@app.post("/api/policy/query")
def api_policy_query(payload: dict, user: M.User = Depends(_current_user)):
    query = payload.get("query", "").strip()
    if not query:
        raise HTTPException(400, "请输入问题")
    state = run_agent("policy_query", {"query": query})
    return {"answer": state["answer"], "hits": state.get("context_docs", [])}


# ---- 2) 员工：提交报销单 + 自动触发材料审核 Agent ----
@app.post("/api/reimburse/submit")
def api_reimburse_submit(
    category: str = Form(...),
    amount: str = Form(...),
    purpose: str = Form(...),
    material: Optional[UploadFile] = File(None),
    user: M.User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    # 1. 校验
    try:
        amount_fen = int(float(amount) * 100)
    except ValueError:
        raise HTTPException(400, "金额格式错误")
    if category not in ("差旅", "招待", "采购"):
        raise HTTPException(400, "类别只能是：差旅/招待/采购")

    # 2. 保存附件
    material_path = ""
    if material and material.filename:
        safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}_{material.filename}"
        dest = os.path.join(UPLOAD_DIR, safe_name)
        with open(dest, "wb") as f:
            f.write(material.file.read())
        material_path = f"/uploads/{safe_name}"

    # 3. 生成单号
    latest = db.query(M.Reimbursement).order_by(M.Reimbursement.id.desc()).first()
    next_id = (latest.id + 1) if latest else 1
    tracking_no = f"R-{next_id:04d}"

    # 4. 写入报销单（初始状态 SUBMITTED）
    rec = M.Reimbursement(
        tracking_no=tracking_no,
        applicant_id=user.id,
        category=category,
        amount=amount_fen,
        purpose=purpose,
        material_file=material_path,
        material_text="",
        status=M.ReimbursementStatus.SUBMITTED,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # 5. 自动触发材料审核 Agent
    state = run_agent("material_audit", {
        "tracking_no": tracking_no,
        "category": category,
        "amount": amount_fen,
        "purpose": purpose,
        "material_text": "",   # 后续可对接 OCR
    })

    # 6. 根据审核结论更新状态 + 写审计日志
    if state["decision"] == "approved":
        rec.status = M.ReimbursementStatus.MATERIAL_APPROVED
    else:
        rec.status = M.ReimbursementStatus.MATERIAL_REJECTED
    db.add(M.AuditLog(
        reimbursement_id=rec.id,
        operator_id=user.id,
        action="material_audit_" + state["decision"],
        comment=state["reason"][:500],
    ))
    db.commit()

    return {
        "tracking_no": tracking_no,
        "status": rec.status.value,
        "decision": state["decision"],
        "reason": state["reason"],
    }


# ---- 3) 员工：查询本人单据状态 ----
@app.get("/api/reimburse/mine")
def api_my_reimbursements(user: M.User = Depends(_current_user),
                          db: Session = Depends(get_db)):
    if user.role != M.UserRole.EMPLOYEE:
        raise HTTPException(403, "仅员工可查")
    rows = db.query(M.Reimbursement).filter_by(applicant_id=user.id)\
        .order_by(M.Reimbursement.created_at.desc()).all()
    return [{
        "tracking_no": r.tracking_no,
        "category": r.category,
        "amount": r.amount / 100,
        "status": r.status.value,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
    } for r in rows]


@app.get("/api/reimburse/{tracking_no}")
def api_reimburse_get(tracking_no: str,
                      user: M.User = Depends(_current_user),
                      db: Session = Depends(get_db)):
    rec = db.query(M.Reimbursement).filter_by(tracking_no=tracking_no).first()
    if not rec:
        raise HTTPException(404, "未找到该单号")
    # 权限：员工只能查自己的；行政/财务可查全部
    if user.role == M.UserRole.EMPLOYEE and rec.applicant_id != user.id:
        raise HTTPException(403, "无权查看他人单据")

    state = run_agent("status_query", {
        "tracking_no": rec.tracking_no,
        "status": rec.status.value,
        "amount": rec.amount,
        "category": rec.category,
    })
    logs = db.query(M.AuditLog).filter_by(reimbursement_id=rec.id)\
        .order_by(M.AuditLog.created_at).all()
    return {
        "tracking_no": rec.tracking_no,
        "category": rec.category,
        "amount": rec.amount / 100,
        "purpose": rec.purpose,
        "status": rec.status.value,
        "answer": state["answer"],
        "logs": [{"action": l.action, "comment": l.comment,
                  "time": l.created_at.strftime("%Y-%m-%d %H:%M")} for l in logs],
    }


# ---- 4) 财务：核对（调用 finance_review Agent）----
@app.post("/api/finance/review/{tracking_no}")
def api_finance_review(tracking_no: str,
                       action: str = Form(...),    # approve / reject / paid
                       comment: str = Form(""),
                       user: M.User = Depends(_current_user),
                       db: Session = Depends(get_db)):
    if user.role != M.UserRole.FINANCE:
        raise HTTPException(403, "仅财务专员可执行")
    rec = db.query(M.Reimbursement).filter_by(tracking_no=tracking_no).first()
    if not rec:
        raise HTTPException(404, "单号不存在")

    # 先让 Agent 给出建议（仅当还没核对过时）
    if rec.status == M.ReimbursementStatus.MATERIAL_APPROVED and action == "advise":
        state = run_agent("finance_review", {
            "tracking_no": rec.tracking_no,
            "category": rec.category,
            "amount": rec.amount,
            "purpose": rec.purpose,
        })
        return {"agent_decision": state["decision"], "agent_reason": state["reason"]}

    # 人工执行
    mapping = {
        "approve": M.ReimbursementStatus.FINANCE_APPROVED,
        "reject":  M.ReimbursementStatus.FINANCE_REJECTED,
        "paid":    M.ReimbursementStatus.PAID,
    }
    if action not in mapping:
        raise HTTPException(400, f"无效操作：{action}")
    rec.status = mapping[action]
    db.add(M.AuditLog(
        reimbursement_id=rec.id,
        operator_id=user.id,
        action=f"finance_{action}",
        comment=comment,
    ))
    db.commit()
    return {"tracking_no": tracking_no, "status": rec.status.value}


# ---- 5) 行政：维护制度文档 ----
@app.post("/api/admin/policy/add")
def api_admin_policy_add(
    title: str = Form(...),
    category: str = Form(...),
    content: str = Form(...),
    user: M.User = Depends(_current_user),
    db: Session = Depends(get_db),
):
    if user.role != M.UserRole.ADMIN:
        raise HTTPException(403, "仅行政专员可操作")
    doc = M.PolicyDoc(title=title.strip(), category=category.strip(),
                      content=content.strip())
    db.add(doc); db.commit()
    count = get_rag().rebuild_index()   # 自动重建索引
    return {"ok": True, "doc_id": doc.id, "chunks": count}


@app.post("/api/admin/policy/{doc_id}/update")
def api_admin_policy_update(doc_id: int,
                            title: str = Form(...),
                            category: str = Form(...),
                            content: str = Form(...),
                            user: M.User = Depends(_current_user),
                            db: Session = Depends(get_db)):
    if user.role != M.UserRole.ADMIN:
        raise HTTPException(403, "仅行政专员可操作")
    doc = db.query(M.PolicyDoc).filter_by(id=doc_id).first()
    if not doc: raise HTTPException(404, "文档不存在")
    doc.title = title.strip()
    doc.category = category.strip()
    doc.content = content.strip()
    db.commit()
    count = get_rag().rebuild_index()
    return {"ok": True, "chunks": count}


@app.post("/api/admin/policy/{doc_id}/delete")
def api_admin_policy_delete(doc_id: int,
                            user: M.User = Depends(_current_user),
                            db: Session = Depends(get_db)):
    if user.role != M.UserRole.ADMIN:
        raise HTTPException(403, "仅行政专员可操作")
    doc = db.query(M.PolicyDoc).filter_by(id=doc_id).first()
    if not doc: raise HTTPException(404, "文档不存在")
    db.delete(doc); db.commit()
    count = get_rag().rebuild_index()
    return {"ok": True, "chunks": count}


@app.post("/api/admin/rebuild")
def api_admin_rebuild(user: M.User = Depends(_current_user)):
    if user.role != M.UserRole.ADMIN:
        raise HTTPException(403, "仅行政专员可操作")
    count = get_rag().rebuild_index()
    return {"ok": True, "chunks": count}


# ============================================================
#   启动脚本
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)