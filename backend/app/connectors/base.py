from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class Customer:
    id: str
    qb_id: str
    name: str
    email: Optional[str]
    phone: Optional[str]
    state: Optional[str]
    balance: Decimal
    total_sales: Decimal


@dataclass
class Invoice:
    id: str
    qb_id: str
    customer_id: str
    customer_name: str
    date: date
    due_date: Optional[date]
    total: Decimal
    balance: Decimal
    status: str
    line_items: list[dict]


@dataclass
class InventoryItem:
    id: str
    qb_id: str
    name: str
    sku: Optional[str]
    quantity_on_hand: Decimal
    unit_price: Decimal
    purchase_cost: Decimal
    reorder_point: Optional[Decimal]


@dataclass
class PurchaseOrder:
    id: str
    qb_id: str
    vendor_id: str
    vendor_name: str
    date: date
    expected_date: Optional[date]
    total: Decimal
    status: str
    line_items: list[dict]


class QuickBooksAdapter(ABC):
    """
    Unified interface for QuickBooks data access.
    Implementations: QBDesktopAdapter (Conductor), QBOnlineAdapter (Intuit API).
    The AI and business logic only interact with this interface.
    """

    @abstractmethod
    async def get_customers(self) -> list[Customer]: ...

    @abstractmethod
    async def get_invoices(
        self, date_from: date, date_to: date
    ) -> list[Invoice]: ...

    @abstractmethod
    async def get_inventory(self) -> list[InventoryItem]: ...

    @abstractmethod
    async def get_purchase_orders(self) -> list[PurchaseOrder]: ...

    @abstractmethod
    async def test_connection(self) -> bool: ...
