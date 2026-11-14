-- Staging Phase 1: index new pricing price-book foreign keys.
-- Additive performance fix; no price, supplier, warehouse or QBO changes.
CREATE INDEX IF NOT EXISTS idx_pricing_price_book_created_by
    ON pricing_price_book(created_by);
CREATE INDEX IF NOT EXISTS idx_pricing_price_book_approved_by
    ON pricing_price_book(approved_by);
CREATE INDEX IF NOT EXISTS idx_pricing_price_book_cost_snapshot
    ON pricing_price_book(company_id,cost_snapshot_id);
