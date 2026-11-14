"""Synthetic, in-memory current-role source used by pricing endpoint tests."""
from types import SimpleNamespace


class RoleQuery:
    def __init__(self, identity: str, company: str, role: str, *, active: bool = True, fail: bool = False):
        self.row = {
            "id": identity, "company_id": company,
            "role": role, "is_active": active,
        }
        self.filters = {}
        self.fail = fail

    def select(self, columns: str):
        assert columns == "id,company_id,role,is_active"
        return self

    def eq(self, field: str, value):
        self.filters[field] = value
        return self

    def limit(self, n: int):
        assert n == 2
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("Synthetic role database outage")
        if any(self.row.get(field) != value for field, value in self.filters.items()):
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[dict(self.row)])


class RoleDB:
    def __init__(self, *, identity: str = "user-1", company: str = "tenant-a",
                 role: str = "owner", active: bool = True, fail: bool = False):
        self.role_query = RoleQuery(identity, company, role, active=active, fail=fail)

    def table(self, name: str):
        assert name == "users"
        return self.role_query
