"""数据库连接 + 种子数据"""
import os
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import (Base, User, UserRole, Reimbursement, ReimbursementStatus,
                    PolicyDoc, AuditLog)

DB_PATH = os.path.join(os.path.dirname(__file__), "baoxiao.db")
ENGINE = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(reset: bool = False):
    """建表并写入初始种子数据"""
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    Base.metadata.create_all(ENGINE)

    db = SessionLocal()
    try:
        # ---- 种子用户（三个角色） ----
        if not db.query(User).first():
            users = [
                User(username="xiaolin", display_name="小林（员工）", role=UserRole.EMPLOYEE),
                User(username="qiaojie", display_name="乔姐（行政专员）", role=UserRole.ADMIN),
                User(username="zhoumin", display_name="周敏（财务专员）", role=UserRole.FINANCE),
            ]
            db.add_all(users)
            db.flush()

        # ---- 种子制度文档（行政专员维护） ----
        if not db.query(PolicyDoc).first():
            docs = [
                PolicyDoc(
                    title="差旅费用报销制度总则",
                    category="差旅",
                    keywords="出差 住宿 交通 餐饮 标准 流程 审批 时效 机票 火车票 出租车 自驾 报销材料",
                    content="""【差旅费用报销制度】

1. 适用范围
本制度适用于公司全体员工因公务出差产生的交通、住宿、餐饮等费用报销。

2. 出差审批
员工出差须提前通过 OA 系统提交出差申请，注明出差事由、时间、地点、预算金额，经部门经理审批后方可成行。超预算 20% 以上须总经理审批。

3. 交通标准
3.1 国内机票：经济舱；连续飞行超过 6 小时可升商务舱（须预先审批）。
3.2 火车：硬卧/二等座；连续乘车超过 6 小时可升软卧/一等座。
3.3 市内交通：出租车单次不超过 80 元，超过需说明原因；网约车需保留电子发票。
3.4 自驾出差：按 1.2 元/公里报销油费，过路费、停车费实报实销。

4. 住宿标准（每晚上限）
一线城市（北京/上海/广州/深圳）：500 元
省会城市：400 元
其他城市：300 元
同性别两人同出差须合住一间，按单人间标准报销。

5. 餐饮标准（每天上限）
一线城市：100 元
省会城市：80 元
其他城市：60 元
招待客户的餐饮费用归入"招待费"另行审批，不占用差旅餐饮额度。

6. 报销材料要求
6.1 交通类：电子机票/火车票/出租车票（含付款凭证）
6.2 住宿类：酒店发票（含入住清单）
6.3 餐饮类：餐饮发票（附消费清单）
6.4 所有票据须注明日期、金额、开票单位，抬头为公司全称。
6.5 境外出差需提供中英文对照发票及汇率换算凭证。

7. 时效要求
出差结束后 15 个工作日内提交报销申请，逾期须说明原因并经部门经理特批。

8. 审批流程
员工提交 → 部门经理审核 → 财务专员核对 → 总经理审批（超预算）→ 付款。""",
                ),
                PolicyDoc(
                    title="招待费用报销制度",
                    category="招待",
                    keywords="招待 客户 餐饮 礼品 审批 预算 人均 酒水 禁止",
                    content="""【招待费用报销制度】

1. 招待对象
仅可用于招待公司客户、合作伙伴、政府主管部门人员等与公司业务相关的外部人员。

2. 审批要求
单次招待预算超过 500 元须提前提交申请，注明招待对象、事由、预计金额，经部门经理审批。超过 2000 元须分管副总审批。

3. 标准上限
3.1 单次人均餐费：80 元（一线城市 100 元）
3.2 酒水费用不超过餐费总额的 30%
3.3 赠送礼品单次不超过 200 元/人，年度累计不超过 1000 元

4. 材料要求
4.1 正规餐饮发票（抬头为公司全称）
4.2 消费清单（含用餐时间、人数、菜品明细）
4.3 招待说明（客户单位、人员名单、商谈事项摘要）

5. 禁止事项
5.1 禁止报销私人招待费用
5.2 禁止用于个人礼金、娱乐消费
5.3 同一场合不得拆分多次报销以规避审批""",
                ),
                PolicyDoc(
                    title="办公采购费用报销制度",
                    category="采购",
                    keywords="采购 办公 用品 招标 报价 发票 验收 审批",
                    content="""【办公采购费用报销制度】

1. 单笔金额
单笔采购超过 2000 元须有两家以上供应商报价对比；超过 5000 元须走正式招标流程。

2. 采购范围
仅限办公用品、低值易耗品、办公设备耗材、印刷装订等与办公直接相关的物品。严禁报销生活用品、福利品。

3. 材料要求
3.1 采购发票（增值税专用发票优先）
3.2 送货单或收货确认单
3.3 超过 1000 元的采购须附付款凭证（对公转账记录）

4. 审批
部门经理审批 → 行政专员验收 → 财务专员核对 → 付款。""",
                ),
            ]
            db.add_all(docs)

        # ---- 示例报销单（员工小林的） ----
        if not db.query(Reimbursement).first():
            xiaolin = db.query(User).filter_by(username="xiaolin").first()
            demo = Reimbursement(
                tracking_no="R-0001",
                applicant_id=xiaolin.id,
                category="差旅",
                amount=280000,  # 2800 元
                purpose="上海客户拜访：交通 800 + 住宿 1500 + 餐饮 500",
                status=ReimbursementStatus.FINANCE_APPROVED,
                created_at=datetime(2025, 12, 10, 9, 30),
                updated_at=datetime(2025, 12, 11, 14, 20),
            )
            db.add(demo)

        db.commit()
        print("✅ 数据库初始化完成，种子数据已写入")
    finally:
        db.close()


if __name__ == "__main__":
    init_db(reset=True)