import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import {
  InventoryAlert,
  PurchaseOrderDetail,
  PurchaseOrderSummary,
  getPurchaseOrder,
  getPurchaseOrders,
  getStockAlerts,
  logisticsErrorMessage,
  purchaseOrderStatusLabel,
  sortStockAlerts,
  urgentAlertCount,
} from '../api/logistics';
import { COLORS, RADIUS, SHADOW } from '../theme';

// ── Helpers ──────────────────────────────────────────────────────────────────

const STOCK_CONFIG = {
  out_of_stock: { color: COLORS.danger,  bg: COLORS.dangerLight,  label: 'Out of stock' },
  critical:     { color: COLORS.danger,  bg: COLORS.dangerLight,  label: 'Critical'     },
  low:          { color: COLORS.warning, bg: COLORS.warningLight, label: 'Low'          },
};

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatWeeks(weeks: number | null): string {
  if (weeks == null || !Number.isFinite(Number(weeks))) return '—';
  return `~${Number(weeks).toFixed(1)} wks`;
}

// ── Shared pieces ─────────────────────────────────────────────────────────────

function ErrorBox({ message }: { message: string }) {
  return (
    <View style={styles.errorBox} accessibilityRole="alert">
      <Ionicons name="alert-circle" size={16} color={COLORS.danger} />
      <Text style={styles.errorText}>{message}</Text>
    </View>
  );
}

function Loading() {
  return (
    <View style={styles.centered}>
      <ActivityIndicator size="large" color={COLORS.primary} />
    </View>
  );
}

// ── Low stock ─────────────────────────────────────────────────────────────────

function StockAlertCard({ alert }: { alert: InventoryAlert }) {
  const cfg = STOCK_CONFIG[alert.stock_status] ?? STOCK_CONFIG.low;
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={[styles.cardTitle, { flex: 1, marginRight: 8 }]} numberOfLines={1}>{alert.product_name}</Text>
        <View style={[styles.badge, { backgroundColor: cfg.bg }]}>
          <Text style={[styles.badgeText, { color: cfg.color }]}>{cfg.label}</Text>
        </View>
      </View>
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>On hand</Text>
          <Text style={styles.statValue}>{Number(alert.total_qty).toLocaleString()} units</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Runs out in</Text>
          <Text style={[styles.statValue, alert.stock_status !== 'low' && { color: COLORS.danger }]}>
            {formatWeeks(alert.weeks_remaining)}
          </Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Needs a PO</Text>
          <Text style={styles.statValue}>{alert.needs_po ? 'Yes' : 'Not yet'}</Text>
        </View>
      </View>
    </View>
  );
}

function StockTab({
  alerts, loading, refreshing, onRefresh, error,
}: {
  alerts: InventoryAlert[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  error: string | null;
}) {
  if (loading) return <Loading />;
  return (
    <ScrollView
      contentContainerStyle={styles.tabContent}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />}
    >
      {error ? (
        <ErrorBox message={error} />
      ) : alerts.length === 0 ? (
        <View style={styles.emptyState}>
          <Ionicons name="checkmark-circle-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No products are low on stock right now.</Text>
        </View>
      ) : (
        alerts.map((a) => <StockAlertCard key={a.item_id || a.product_name} alert={a} />)
      )}
    </ScrollView>
  );
}

// ── Purchase orders ───────────────────────────────────────────────────────────

function PurchaseOrderCard({ order }: { order: PurchaseOrderSummary }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<PurchaseOrderDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const toggle = useCallback(async () => {
    const next = !open;
    setOpen(next);
    if (!next || detail || detailLoading) return;
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await getPurchaseOrder(order.po_number));
    } catch (err) {
      setDetailError(logisticsErrorMessage(err, 'this purchase order'));
    } finally {
      setDetailLoading(false);
    }
  }, [open, detail, detailLoading, order.po_number]);

  return (
    <TouchableOpacity style={styles.card} onPress={toggle} activeOpacity={0.8} accessibilityRole="button">
      <View style={styles.cardHeader}>
        <View style={{ flex: 1, marginRight: 8 }}>
          <Text style={styles.cardTitle} numberOfLines={1}>{order.po_number}</Text>
          <Text style={styles.cardSubtitle} numberOfLines={1}>{order.supplier_ref || 'Supplier not recorded'}</Text>
        </View>
        <View style={[styles.badge, { backgroundColor: COLORS.primaryLight }]}>
          <Text style={[styles.badgeText, { color: COLORS.primary }]}>{purchaseOrderStatusLabel(order.status)}</Text>
        </View>
      </View>
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Created</Text>
          <Text style={styles.statValue}>{formatDate(order.created_at)}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Approved</Text>
          <Text style={styles.statValue}>{order.approved_at ? formatDate(order.approved_at) : 'Not yet'}</Text>
        </View>
        <Ionicons name={open ? 'chevron-up' : 'chevron-down'} size={16} color={COLORS.textMuted} />
      </View>

      {open && (
        <View style={styles.detail}>
          {detailLoading ? (
            <ActivityIndicator size="small" color={COLORS.primary} />
          ) : detailError ? (
            <ErrorBox message={detailError} />
          ) : detail ? (
            <>
              <View style={styles.statsRow}>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Cases ordered</Text>
                  <Text style={styles.statValue}>{detail.ordered_cases}</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Received</Text>
                  <Text style={styles.statValue}>{detail.received_cases}</Text>
                </View>
                <View style={styles.stat}>
                  <Text style={styles.statLabel}>Still to come</Text>
                  <Text style={styles.statValue}>{detail.remaining_cases}</Text>
                </View>
              </View>
              {!detail.accounting_reconciled && (
                <Text style={styles.note}>Not yet checked against QuickBooks.</Text>
              )}
            </>
          ) : null}
        </View>
      )}
    </TouchableOpacity>
  );
}

function OrdersTab({
  orders, loading, refreshing, onRefresh, error,
}: {
  orders: PurchaseOrderSummary[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  error: string | null;
}) {
  if (loading) return <Loading />;
  return (
    <ScrollView
      contentContainerStyle={styles.tabContent}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />}
    >
      {error ? (
        <ErrorBox message={error} />
      ) : orders.length === 0 ? (
        <View style={styles.emptyState}>
          <Ionicons name="document-text-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No saved purchase orders yet.</Text>
        </View>
      ) : (
        orders.map((o) => <PurchaseOrderCard key={o.id || o.po_number} order={o} />)
      )}
    </ScrollView>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

type TabKey = 'Low stock' | 'Purchase orders';
const TABS: TabKey[] = ['Low stock', 'Purchase orders'];

export default function LogisticsScreen() {
  const [activeTab, setActiveTab] = useState<TabKey>('Low stock');

  const [alerts, setAlerts] = useState<InventoryAlert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsRefreshing, setAlertsRefreshing] = useState(false);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  const [orders, setOrders] = useState<PurchaseOrderSummary[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(true);
  const [ordersRefreshing, setOrdersRefreshing] = useState(false);
  const [ordersError, setOrdersError] = useState<string | null>(null);

  const loadAlerts = useCallback(async () => {
    try {
      setAlertsError(null);
      setAlerts(sortStockAlerts(await getStockAlerts()));
    } catch (err) {
      // A failed read is never shown as "nothing low on stock".
      setAlerts([]);
      setAlertsError(logisticsErrorMessage(err, 'stock alerts'));
    } finally {
      setAlertsLoading(false);
      setAlertsRefreshing(false);
    }
  }, []);

  const loadOrders = useCallback(async () => {
    try {
      setOrdersError(null);
      setOrders(await getPurchaseOrders());
    } catch (err) {
      setOrders([]);
      setOrdersError(logisticsErrorMessage(err, 'purchase orders'));
    } finally {
      setOrdersLoading(false);
      setOrdersRefreshing(false);
    }
  }, []);

  useEffect(() => { loadAlerts(); }, [loadAlerts]);
  useEffect(() => { loadOrders(); }, [loadOrders]);

  const urgentCount = urgentAlertCount(alerts);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Logistics</Text>
        <View style={styles.alertRow}>
          {urgentCount > 0 && (
            <View style={[styles.alertChip, { backgroundColor: COLORS.dangerLight }]}>
              <Text style={[styles.alertChipText, { color: COLORS.danger }]}>
                {urgentCount} urgent
              </Text>
            </View>
          )}
        </View>
      </View>

      <View style={styles.tabBar}>
        {TABS.map((tab) => (
          <TouchableOpacity
            key={tab}
            style={[styles.tabItem, activeTab === tab && styles.tabItemActive]}
            onPress={() => setActiveTab(tab)}
            activeOpacity={0.7}
          >
            <Text style={[styles.tabLabel, activeTab === tab && styles.tabLabelActive]}>
              {tab}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {activeTab === 'Low stock' && (
        <StockTab
          alerts={alerts}
          loading={alertsLoading}
          refreshing={alertsRefreshing}
          onRefresh={() => { setAlertsRefreshing(true); loadAlerts(); }}
          error={alertsError}
        />
      )}
      {activeTab === 'Purchase orders' && (
        <OrdersTab
          orders={orders}
          loading={ordersLoading}
          refreshing={ordersRefreshing}
          onRefresh={() => { setOrdersRefreshing(true); loadOrders(); }}
          error={ordersError}
        />
      )}
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container:        { flex: 1, backgroundColor: COLORS.background },
  centered:         { flex: 1, justifyContent: 'center', alignItems: 'center' },

  header:           { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  title:            { fontSize: 22, fontWeight: '800', color: COLORS.text },
  alertRow:         { flexDirection: 'row', gap: 6, marginTop: 4 },
  alertChip:        { borderRadius: 99, paddingHorizontal: 10, paddingVertical: 2 },
  alertChipText:    { fontSize: 12, fontWeight: '700' },

  tabBar: {
    flexDirection: 'row',
    marginHorizontal: 16,
    marginBottom: 8,
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.border,
    overflow: 'hidden',
  },
  tabItem:          { flex: 1, paddingVertical: 9, alignItems: 'center' },
  tabItemActive:    { backgroundColor: COLORS.primary },
  tabLabel:         { fontSize: 13, fontWeight: '600', color: COLORS.textSecondary },
  tabLabelActive:   { color: '#fff' },

  tabContent: { paddingHorizontal: 16, paddingBottom: 32, paddingTop: 4 },

  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 14,
    marginBottom: 10,
    ...SHADOW.sm,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  cardTitle:    { fontSize: 15, fontWeight: '600', color: COLORS.text },
  cardSubtitle: { fontSize: 12, color: COLORS.textMuted, marginTop: 1 },

  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 99,
    paddingHorizontal: 8,
    paddingVertical: 3,
    gap: 3,
  },
  badgeText: { fontSize: 11, fontWeight: '700' },

  statsRow:  { flexDirection: 'row', gap: 8, alignItems: 'center' },
  stat:      { flex: 1 },
  statLabel: { fontSize: 11, color: COLORS.textMuted, marginBottom: 2 },
  statValue: { fontSize: 13, fontWeight: '600', color: COLORS.text },

  detail: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  note: { fontSize: 11, color: COLORS.textSecondary, marginTop: 8 },

  emptyState: { alignItems: 'center', paddingTop: 60, gap: 10 },
  emptyText:  { color: COLORS.textMuted, fontSize: 14, textAlign: 'center', maxWidth: 260 },

  errorBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 6,
    backgroundColor: COLORS.dangerLight,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 10,
  },
  errorText: { color: COLORS.danger, fontSize: 13, flex: 1 },
});
