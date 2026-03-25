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
  CostVariance,
  ReorderAlert,
  ShipmentStatus,
  getCostVariances,
  getReorderAlerts,
  getShipments,
} from '../api/logistics';
import { COLORS, RADIUS, SHADOW } from '../theme';

// ── Helpers ──────────────────────────────────────────────────────────────────

const URGENCY_CONFIG = {
  red:    { color: COLORS.danger,  bg: COLORS.dangerLight,  label: 'Urgent'  },
  yellow: { color: COLORS.warning, bg: COLORS.warningLight, label: 'Watch'   },
  green:  { color: COLORS.success, bg: COLORS.successLight, label: 'OK'      },
};

const SEVERITY_CONFIG = {
  info:     { color: COLORS.textSecondary, bg: COLORS.border        },
  warning:  { color: COLORS.warning,       bg: COLORS.warningLight  },
  alert:    { color: COLORS.danger,        bg: COLORS.dangerLight   },
  critical: { color: COLORS.danger,        bg: COLORS.dangerLight   },
};

function formatCurrency(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function formatPct(n: number): string {
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ReorderCard({ alert }: { alert: ReorderAlert }) {
  const cfg = URGENCY_CONFIG[alert.urgency];
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle} numberOfLines={1}>{alert.sku}</Text>
        <View style={[styles.badge, { backgroundColor: cfg.bg }]}>
          <Text style={[styles.badgeText, { color: cfg.color }]}>{cfg.label}</Text>
        </View>
      </View>
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Days of Supply</Text>
          <Text style={[styles.statValue, alert.urgency === 'red' && { color: COLORS.danger }]}>
            {alert.days_of_supply}d
          </Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Rec. Order Qty</Text>
          <Text style={styles.statValue}>{alert.recommended_qty.toLocaleString()}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Lead Time P80</Text>
          <Text style={styles.statValue}>{alert.lead_time_p80_days}d</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Container Fill</Text>
          <Text style={styles.statValue}>{alert.container_fill_pct.toFixed(0)}%</Text>
        </View>
      </View>
    </View>
  );
}

function ShipmentCard({ shipment }: { shipment: ShipmentStatus }) {
  const isDelayed = shipment.delay_days > 0;
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={{ flex: 1, marginRight: 8 }}>
          <Text style={styles.cardTitle} numberOfLines={1}>{shipment.booking_ref}</Text>
          <Text style={styles.cardSubtitle}>{shipment.vessel_name}</Text>
        </View>
        {isDelayed ? (
          <View style={[styles.badge, { backgroundColor: COLORS.dangerLight }]}>
            <Ionicons name="alert-circle" size={11} color={COLORS.danger} />
            <Text style={[styles.badgeText, { color: COLORS.danger }]}>
              +{shipment.delay_days}d delay
            </Text>
          </View>
        ) : (
          <View style={[styles.badge, { backgroundColor: COLORS.successLight }]}>
            <Ionicons name="checkmark-circle" size={11} color={COLORS.success} />
            <Text style={[styles.badgeText, { color: COLORS.success }]}>On Time</Text>
          </View>
        )}
      </View>
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>ETA</Text>
          <Text style={styles.statValue}>{shipment.eta}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Status</Text>
          <Text style={styles.statValue}>{shipment.status}</Text>
        </View>
      </View>
    </View>
  );
}

function CostVarianceRow({ item }: { item: CostVariance }) {
  const cfg = SEVERITY_CONFIG[item.severity];
  const isNegative = item.change_pct < 0;
  return (
    <View style={styles.costRow}>
      <View style={{ flex: 1 }}>
        <Text style={styles.cardTitle} numberOfLines={1}>{item.component}</Text>
      </View>
      <View style={styles.costRight}>
        <Text style={[styles.costPct, { color: isNegative ? COLORS.success : COLORS.danger }]}>
          {formatPct(item.change_pct)}
        </Text>
        <Text style={styles.costImpact}>{formatCurrency(item.annualized_impact)}/yr</Text>
        <View style={[styles.badge, { backgroundColor: cfg.bg, marginLeft: 6 }]}>
          <Text style={[styles.badgeText, { color: cfg.color }]}>{item.severity}</Text>
        </View>
      </View>
    </View>
  );
}

// ── Tab content components ────────────────────────────────────────────────────

function ReorderTab({
  alerts,
  loading,
  refreshing,
  onRefresh,
  error,
}: {
  alerts: ReorderAlert[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  error: string | null;
}) {
  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }
  return (
    <ScrollView
      contentContainerStyle={styles.tabContent}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />
      }
    >
      {error ? <View style={styles.errorBox}><Text style={styles.errorText}>{error}</Text></View> : null}
      {alerts.length === 0 && !error ? (
        <View style={styles.emptyState}>
          <Ionicons name="checkmark-circle-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No reorder alerts — all SKUs are well stocked.</Text>
        </View>
      ) : (
        alerts.map((a) => <ReorderCard key={a.sku} alert={a} />)
      )}
    </ScrollView>
  );
}

function ShipmentsTab({
  shipments,
  loading,
  refreshing,
  onRefresh,
  error,
}: {
  shipments: ShipmentStatus[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  error: string | null;
}) {
  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }
  return (
    <ScrollView
      contentContainerStyle={styles.tabContent}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />
      }
    >
      {error ? <View style={styles.errorBox}><Text style={styles.errorText}>{error}</Text></View> : null}
      {shipments.length === 0 && !error ? (
        <View style={styles.emptyState}>
          <Ionicons name="boat-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No active shipments.</Text>
        </View>
      ) : (
        shipments.map((s) => <ShipmentCard key={s.booking_ref} shipment={s} />)
      )}
    </ScrollView>
  );
}

function CostsTab({
  variances,
  totalLandedCost,
  loading,
  refreshing,
  onRefresh,
  error,
}: {
  variances: CostVariance[];
  totalLandedCost: number | null;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  error: string | null;
}) {
  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={COLORS.primary} />
      </View>
    );
  }
  return (
    <ScrollView
      contentContainerStyle={styles.tabContent}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />
      }
    >
      {error ? <View style={styles.errorBox}><Text style={styles.errorText}>{error}</Text></View> : null}

      {/* Summary card */}
      {totalLandedCost != null && (
        <View style={[styles.summaryCard, SHADOW.md]}>
          <Text style={styles.summaryLabel}>Total Landed Cost</Text>
          <Text style={styles.summaryValue}>{formatCurrency(totalLandedCost)}</Text>
          <Text style={styles.summaryCaption}>Annualised across all SKUs</Text>
        </View>
      )}

      {variances.length === 0 && !error ? (
        <View style={styles.emptyState}>
          <Ionicons name="trending-up-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No cost variances detected.</Text>
        </View>
      ) : (
        <View style={styles.card}>
          {variances.map((v, i) => (
            <React.Fragment key={v.component}>
              <CostVarianceRow item={v} />
              {i < variances.length - 1 && <View style={styles.divider} />}
            </React.Fragment>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

// ── Main screen ───────────────────────────────────────────────────────────────

type TabKey = 'Reorder' | 'Shipments' | 'Costs';
const TABS: TabKey[] = ['Reorder', 'Shipments', 'Costs'];

export default function LogisticsScreen() {
  const [activeTab, setActiveTab] = useState<TabKey>('Reorder');

  // Reorder state
  const [alerts, setAlerts] = useState<ReorderAlert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsRefreshing, setAlertsRefreshing] = useState(false);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  // Shipments state
  const [shipments, setShipments] = useState<ShipmentStatus[]>([]);
  const [shipmentsLoading, setShipmentsLoading] = useState(true);
  const [shipmentsRefreshing, setShipmentsRefreshing] = useState(false);
  const [shipmentsError, setShipmentsError] = useState<string | null>(null);

  // Costs state
  const [variances, setVariances] = useState<CostVariance[]>([]);
  const [totalLandedCost, setTotalLandedCost] = useState<number | null>(null);
  const [costsLoading, setCostsLoading] = useState(true);
  const [costsRefreshing, setCostsRefreshing] = useState(false);
  const [costsError, setCostsError] = useState<string | null>(null);

  const loadAlerts = useCallback(async () => {
    try {
      setAlertsError(null);
      const data = await getReorderAlerts();
      setAlerts(data);
    } catch (err) {
      setAlertsError(err instanceof Error ? err.message : 'Failed to load reorder alerts');
    } finally {
      setAlertsLoading(false);
      setAlertsRefreshing(false);
    }
  }, []);

  const loadShipments = useCallback(async () => {
    try {
      setShipmentsError(null);
      const data = await getShipments();
      setShipments(data);
    } catch (err) {
      setShipmentsError(err instanceof Error ? err.message : 'Failed to load shipments');
    } finally {
      setShipmentsLoading(false);
      setShipmentsRefreshing(false);
    }
  }, []);

  const loadCosts = useCallback(async () => {
    try {
      setCostsError(null);
      const data = await getCostVariances();
      setVariances(data.variances);
      setTotalLandedCost(data.total_landed_cost);
    } catch (err) {
      setCostsError(err instanceof Error ? err.message : 'Failed to load cost variances');
    } finally {
      setCostsLoading(false);
      setCostsRefreshing(false);
    }
  }, []);

  useEffect(() => { loadAlerts(); }, [loadAlerts]);
  useEffect(() => { loadShipments(); }, [loadShipments]);
  useEffect(() => { loadCosts(); }, [loadCosts]);

  // Urgency summary for header badges
  const urgentCount = alerts.filter((a) => a.urgency === 'red').length;
  const delayedCount = shipments.filter((s) => s.delay_days > 0).length;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
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
          {delayedCount > 0 && (
            <View style={[styles.alertChip, { backgroundColor: COLORS.warningLight }]}>
              <Text style={[styles.alertChipText, { color: COLORS.warning }]}>
                {delayedCount} delayed
              </Text>
            </View>
          )}
        </View>
      </View>

      {/* Tab bar */}
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

      {/* Tab content */}
      {activeTab === 'Reorder' && (
        <ReorderTab
          alerts={alerts}
          loading={alertsLoading}
          refreshing={alertsRefreshing}
          onRefresh={() => { setAlertsRefreshing(true); loadAlerts(); }}
          error={alertsError}
        />
      )}
      {activeTab === 'Shipments' && (
        <ShipmentsTab
          shipments={shipments}
          loading={shipmentsLoading}
          refreshing={shipmentsRefreshing}
          onRefresh={() => { setShipmentsRefreshing(true); loadShipments(); }}
          error={shipmentsError}
        />
      )}
      {activeTab === 'Costs' && (
        <CostsTab
          variances={variances}
          totalLandedCost={totalLandedCost}
          loading={costsLoading}
          refreshing={costsRefreshing}
          onRefresh={() => { setCostsRefreshing(true); loadCosts(); }}
          error={costsError}
        />
      )}
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container:        { flex: 1, backgroundColor: COLORS.background },
  centered:         { flex: 1, justifyContent: 'center', alignItems: 'center' },

  // Header
  header:           { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  title:            { fontSize: 22, fontWeight: '800', color: COLORS.text },
  alertRow:         { flexDirection: 'row', gap: 6, marginTop: 4 },
  alertChip:        { borderRadius: 99, paddingHorizontal: 10, paddingVertical: 2 },
  alertChipText:    { fontSize: 12, fontWeight: '700' },

  // Tab bar
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
  tabItem: {
    flex: 1,
    paddingVertical: 9,
    alignItems: 'center',
  },
  tabItemActive: {
    backgroundColor: COLORS.primary,
  },
  tabLabel: {
    fontSize: 13,
    fontWeight: '600',
    color: COLORS.textSecondary,
  },
  tabLabelActive: {
    color: '#fff',
  },

  // Tab scroll content
  tabContent: { paddingHorizontal: 16, paddingBottom: 32, paddingTop: 4 },

  // Cards
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

  // Badge
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 99,
    paddingHorizontal: 8,
    paddingVertical: 3,
    gap: 3,
  },
  badgeText: { fontSize: 11, fontWeight: '700' },

  // Stats row
  statsRow:  { flexDirection: 'row', gap: 8 },
  stat:      { flex: 1 },
  statLabel: { fontSize: 11, color: COLORS.textMuted, marginBottom: 2 },
  statValue: { fontSize: 13, fontWeight: '600', color: COLORS.text },

  // Cost rows
  costRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
  },
  costRight: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  costPct:    { fontSize: 13, fontWeight: '700', minWidth: 48, textAlign: 'right' },
  costImpact: { fontSize: 12, color: COLORS.textSecondary, marginLeft: 8 },
  divider:    { height: 1, backgroundColor: COLORS.border },

  // Summary card (Costs tab)
  summaryCard: {
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.lg,
    padding: 16,
    marginBottom: 12,
  },
  summaryLabel:   { fontSize: 12, color: 'rgba(255,255,255,0.75)', fontWeight: '500' },
  summaryValue:   { fontSize: 28, fontWeight: '800', color: '#fff', marginTop: 2 },
  summaryCaption: { fontSize: 11, color: 'rgba(255,255,255,0.65)', marginTop: 2 },

  // Empty state
  emptyState: { alignItems: 'center', paddingTop: 60, gap: 10 },
  emptyText:  { color: COLORS.textMuted, fontSize: 14, textAlign: 'center', maxWidth: 260 },

  // Error
  errorBox: {
    backgroundColor: COLORS.dangerLight,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 10,
  },
  errorText: { color: COLORS.danger, fontSize: 13 },
});
