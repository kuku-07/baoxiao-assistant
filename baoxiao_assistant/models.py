"""数据模型 - 青禾报销助手_qxy"""
from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class UserRole(str, Enum):
    EMPLOYEE = "employee"   # 员工（小林）
    ADMIN = "admin"         # 行政专员（乔姐）
    FINANCE = "finance"     # 财务专员（周敏）


class ReimbursementStatus(str, Enum):
    DRAFT = "draft"                 # 草稿
    SUBMITTED = "submitted"         # 已提交待材料审核
    MATERIAL_APPROVED = "material_approved"  # 材料审核通过，待财务核对
    MATERIAL_REJECTED = "material_rejected"  # 材料被退回
    FINANCE_APPROVED = "finance_approved"     # 财务核对通过，待付款
    FINANCE_REJECTED = "finance_rejected"     # 财务退回
    PAID = "paid"                   # 已付款/完成


class User(Base):
    """用户 - 三位角色"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(50), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False)
    password_hash = Column(String(128), default="")

    reimbursements = relationship("Reimbursement", back_populates="applicant")
    audit_logs = relationship("AuditLog", back_populates="operator")


class PolicyDoc(Base):
    """行政专员维护的制度文档"""
    __tablename__ = "policy_docs"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)           # 纯文本内容（切块后存入 FAISS）
    file_path = Column(String(500), default="")     # 原始上传文件路径
    category = Column(String(100), default="差旅")    # 差旅/招待/采购/...
    keywords = Column(String(500), default="")       # 关键词，方便检索
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Reimbursement(Base):
    """报销单"""
    __tablename__ = "reimbursements"
    id = Column(Integer, primary_key=True)
    tracking_no = Column(String(30), unique=True, nullable=False)  # 单号如 R-0001
    applicant_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(String(50), nullable=False)   # 差旅/招待/采购
    amount = Column(Integer, nullable=False)         # 分
    purpose = Column(Text, default="")
    material_file = Column(String(500), default="")  # 上传材料路径
    material_text = Column(Text, default="")         # 材料 OCR/识别后的文本
    status = Column(SAEnum(ReimbursementStatus), default=ReimbursementStatus.DRAFT)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    applicant = relationship("User", back_populates="reimbursements")
    audit_logs = relationship("AuditLog", back_populates="reimbursement",
                              cascade="all, delete-orphan")


class AuditLog(Base):
    """报销单审计日志"""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    reimbursement_id = Column(Integer, ForeignKey("reimbursements.id"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(50), nullable=False)     # submit / approve / reject / ...
    comment = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    reimbursement = relationship("Reimbursement", back_populates="audit_logs")
    operator = relationship("User", back_populates="audit_logs")


class ChatSession(Base):
    """对话会话 - 供员工/行政/财务使用"""
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_key = Column(String(100), unique=True, nullable=False)
    role = Column(String(30), nullable=False)       # policy_inquiry / material_audit / finance_review
    created_at = Column(DateTime, default=datetime.utcnow)