import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum, JSON, Index
from sqlalchemy.orm import relationship
from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    seller = "seller"


class LeadStatus(str, enum.Enum):
    new = "new"
    assigned = "assigned"
    calling = "calling"
    confirmed = "confirmed"
    declined = "declined"
    followup = "followup"
    no_answer = "no_answer"
    waiting = "waiting"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.seller)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    assigned_leads = relationship("Lead", back_populates="assigned_seller", lazy="selectin")
    call_logs = relationship("CallLog", back_populates="seller", lazy="selectin")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_status", "status"),
        Index("ix_leads_assigned_to", "assigned_to"),
        Index("ix_leads_website_status", "website_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    categories = Column(JSON, default=list)
    address = Column(Text, default="")
    phone = Column(String(100), default="")
    email = Column(String(255), default="")
    website = Column(String(500), default="")
    website_status = Column(String(50), default="unknown")
    website_platform = Column(String(100), default="")
    social_links = Column(JSON, default=list)
    rating = Column(String(20), default="")
    reviews = Column(String(20), default="")
    hours = Column(String(255), default="")
    yandex_url = Column(Text, default="")
    source = Column(String(50), default="yandex_maps")
    scraped_at = Column(DateTime, nullable=True)

    status = Column(Enum(LeadStatus), default=LeadStatus.new, nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = Column(Text, default="")

    assigned_seller = relationship("User", back_populates="assigned_leads")
    call_logs = relationship("CallLog", back_populates="lead", lazy="selectin", order_by="CallLog.created_at.desc()")


class CallLog(Base):
    __tablename__ = "call_logs"
    __table_args__ = (Index("ix_call_logs_seller_time", "seller_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(50), nullable=False)
    old_status = Column(String(50), nullable=True)
    new_status = Column(String(50), nullable=True)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    lead = relationship("Lead", back_populates="call_logs")
    seller = relationship("User", back_populates="call_logs")


class PortfolioCategory(Base):
    __tablename__ = "portfolio_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    slug = Column(String(150), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    projects = relationship("PortfolioProject", back_populates="category", lazy="selectin")


class PortfolioProject(Base):
    __tablename__ = "portfolio_projects"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("portfolio_categories.id"), nullable=False)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    client_name = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    category = relationship("PortfolioCategory", back_populates="projects")
    screenshots = relationship("PortfolioScreenshot", back_populates="project", lazy="selectin", order_by="PortfolioScreenshot.sort_order")


class PortfolioScreenshot(Base):
    __tablename__ = "portfolio_screenshots"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("portfolio_projects.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), default="")
    file_path = Column(String(500), nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("PortfolioProject", back_populates="screenshots")
