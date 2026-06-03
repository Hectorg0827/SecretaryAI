import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
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
  ComplianceAlert,
  ComplianceDeadline,
  getAlerts,
  getComplianceStatus,
  getDeadlines,
  getDigest,
} from '../api/compliance';
import { COLORS, RADIUS, SHADOW } from '../theme';

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatCurrency(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000)     return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

const PRIORITY_CONFIG = {
  critical: { color: COLORS.danger,        bg: COLORS.dangerLight,  label: 'Critical' },
  warning:  { color: COLORS.warning,       bg: COLORS.warningLight, label: 'Warning'  },
  info:     { color: COLORS.textSecondary, bg: COLORS.border,       label: 'Info'     },
};

const STATUS_CONFIG = {
  ok:       { color: COLORS.success, bg: COLORS.successLight, label: 'OK'       },
  warning:  { color: COLORS.warning, bg: COLORS.warningLight, label: 'WARNING'  },
  critical: { color: COLORS.danger,  bg: COLORS.dangerLight,  label: 'CRITICAL' },
};

// ── Sub-components ─────────────────────────────────────────────────────────────

function AlertCard({ alert }: { alert: ComplianceAlert }) {
  const cfg = PRIORITY_CONFIG[alert.priority];
  const isCritical = alert.priority === 'critical';
  return (
    <View style={[styles.card, isCritical && styles.cardCritical]}>
      <View style={[styles.priorityBar, { backgroundColor: cfg.color }]} />
      <View style={styles.cardInner}>
        <View style={styles.cardHeader}>
          <Text style={styles.cardTitle} numberOfLines={1}>{alert.item_name}</Text>
          <View style={[styles.badge, { backgroundColor: cfg.bg }]}>
            <Text style={[styles.badgeText, { color: cfg.color }]}>{cfg.label}</Text>
          </View>
        </View>
        <Text style={styles.alertMessage}>{alert.message}</Text>
        <View style={styles.statsRow}>
          <View style={styles.stat}>
            <Text style={styles.statLabel}>State</Text>
            <Text style={styles.statValue}>{alert.state_code}</Text>
          </View>
          <View style={styles.stat}>
            <Text style={styles.statLabel}>Days Until</Text>
            <Text style={[styles.statValue, alert.days_until <= 7 && { color: COLORS.danger }]}>
              {alert.days_until}d
            </Text>
          </View>
          <View style={styles.stat}>
            <Text style={styles.statLabel}>Est. Fee</Text>
            <Text style={styles.statValue}>{formatCurrency(alert.estimated_fee)}</Text>
          </View>
        </View>
        <Text style={styles.actionText}>{alert.action_required}</Text>
      </View>
    </View>
  );
}

function DeadlineCard({ deadline }: { deadline: ComplianceDeadline }) {
  const dueDate = new Date(deadline.due_date);
  const today = new Date();
  const diffDays = Math.ceil((dueDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  const isUrgent = diffDays <= 7;

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={{ flex: 1, marginRight: 8 }}>
          <Text style={styles.cardTitle} numberOfLines={1}>{deadline.deadline_type}</Text>
          <Text style={styles.cardSubtitle}>{deadline.frequency}</Text>
        </View>
        <View style={[styles.stateChip, { backgroundColor: COLORS.border }]}>
          <Text style={[styles.stateChipText, { color: COLORS.textSecondary }]}>
            {deadline.state_code}
          </Text>
        </View>
      </View>
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Due Date</Text>
          <Text style={[styles.statValue, isUrgent && { color: COLORS.danger }]}>
            {deadline.due_date}
          </Text>
        </View>
      </View>
      <Text style={styles.actionText}>{deadline.action}</Text>
    </View>
  );
}

// ── Tab content components ────────────────────────────────────────────────────

function AlertsTab({
  alerts,
  loading,
  refreshing,
  onRefresh,
  error,
  criticalCount,
  warningCount,
}: {
  alerts: ComplianceAlert[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  error: string | null;
  criticalCount: number;
  warningCount: number;
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

      {/* Summary chips */}
      {(criticalCount > 0 || warningCount > 0) && (
        <View style={styles.chipRow}>
          {criticalCount > 0 && (
            <View style={[styles.alertChip, { backgroundColor: COLORS.dangerLight }]}>
              <Ionicons name="alert-circle" size={13} color={COLORS.danger} />
              <Text style={[styles.alertChipText, { color: COLORS.danger }]}>
                {criticalCount} critical
              </Text>
            </View>
          )}
          {warningCount > 0 && (
            <View style={[styles.alertChip, { backgroundColor: COLORS.warningLight }]}>
              <Ionicons name="warning" size={13} color={COLORS.warning} />
              <Text style={[styles.alertChipText, { color: COLORS.warning }]}>
                {warningCount} warning
              </Text>
            </View>
          )}
        </View>
      )}

      {alerts.length === 0 && !error ? (
        <View style={styles.emptyState}>
          <Ionicons name="shield-checkmark-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No compliance alerts — all filings are up to date.</Text>
        </View>
      ) : (
        alerts.map((a) => <AlertCard key={a.id} alert={a} />)
      )}
    </ScrollView>
  );
}

function DeadlinesTab({
  deadlines,
  loading,
  refreshing,
  onRefresh,
  error,
}: {
  deadlines: ComplianceDeadline[];
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
      {deadlines.length === 0 && !error ? (
        <View style={styles.emptyState}>
          <Ionicons name="shield-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No upcoming deadlines in the next 30 days.</Text>
        </View>
      ) : (
        deadlines.map((d, i) => (
          <DeadlineCard key={`${d.state_code}-${d.deadline_type}-${i}`} deadline={d} />
        ))
      )}
    </ScrollView>
  );
}

function CostsTab({
  licensesCost,
  brandRegCost,
  grandTotal,
  loading,
  refreshing,
  onRefresh,
  error,
}: {
  licensesCost: number | null;
  brandRegCost: number | null;
  grandTotal: number | null;
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

  function handleCheckShipment() {
    Alert.alert(
      'Check Shipment Compliance',
      'Enter shipment details to check compliance requirements for your target states.',
      [{ text: 'OK' }]
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

      {/* Total summary card */}
      {grandTotal != null && (
        <View style={[styles.summaryCard, SHADOW.md]}>
          <Text style={styles.summaryLabel}>Total Annual Compliance Cost</Text>
          <Text style={styles.summaryValue}>{formatCurrency(grandTotal)}</Text>
          <Text style={styles.summaryCaption}>Licenses + brand registrations across all active states</Text>
        </View>
      )}

      {/* Breakdown cards */}
      {(licensesCost != null || brandRegCost != null) && (
        <View style={[styles.card, SHADOW.sm]}>
          {licensesCost != null && (
            <>
              <View style={styles.costRow}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.cardTitle}>Annual Licenses</Text>
                  <Text style={styles.cardSubtitle}>State distribution licenses</Text>
                </View>
                <Text style={styles.costAmount}>{formatCurrency(licensesCost)}</Text>
              </View>
              {brandRegCost != null && <View style={styles.divider} />}
            </>
          )}
          {brandRegCost != null && (
            <View style={styles.costRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.cardTitle}>Brand Registrations</Text>
                <Text style={styles.cardSubtitle}>Per-brand, per-state registrations</Text>
              </View>
              <Text style={styles.costAmount}>{formatCurrency(brandRegCost)}</Text>
            </View>
          )}
        </View>
      )}

      {/* Excise tax note */}
      <View style={[styles.noteCard, { backgroundColor: COLORS.warningLight }]}>
        <Ionicons name="information-circle-outline" size={16} color={COLORS.warning} />
        <Text style={[styles.noteText, { color: COLORS.warning }]}>
          Excise taxes are calculated separately at time of sale and are not included in the estimates above.
        </Text>
      </View>

      {/* Check shipment button */}
      <TouchableOpacity style={[styles.primaryButton, SHADOW.sm]} onPress={handleCheckShipment} activeOpacity={0.8}>
        <Ionicons name="shield-checkmark-outline" size={18} color="#fff" />
        <Text style={styles.primaryButtonText}>Check Shipment Compliance</Text>
      </TouchableOpacity>

      {grandTotal == null && licensesCost == null && !error && (
        <View style={styles.emptyState}>
          <Ionicons name="shield-outline" size={40} color={COLORS.textMuted} />
          <Text style={styles.emptyText}>No cost estimate data available.</Text>
        </View>
      )}
    </ScrollView>
  );
}

// ── Main screen ────────────────────────────────────────────────────────────────

type TabKey = 'Alerts' | 'Deadlines' | 'Costs';
const TABS: TabKey[] = ['Alerts', 'Deadlines', 'Costs'];

export default function ComplianceScreen() {
  const [activeTab, setActiveTab] = useState<TabKey>('Alerts');

  // Status state
  const [overallStatus, setOverallStatus] = useState<'ok' | 'warning' | 'critical' | null>(null);

  // Alerts state
  const [alerts, setAlerts] = useState<ComplianceAlert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsRefreshing, setAlertsRefreshing] = useState(false);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  // Deadlines state
  const [deadlines, setDeadlines] = useState<ComplianceDeadline[]>([]);
  const [deadlinesLoading, setDeadlinesLoading] = useState(true);
  const [deadlinesRefreshing, setDeadlinesRefreshing] = useState(false);
  const [deadlinesError, setDeadlinesError] = useState<string | null>(null);

  // Costs state
  const [licensesCost, setLicensesCost] = useState<number | null>(null);
  const [brandRegCost, setBrandRegCost] = useState<number | null>(null);
  const [grandTotal, setGrandTotal] = useState<number | null>(null);
  const [costsLoading, setCostsLoading] = useState(true);
  const [costsRefreshing, setCostsRefreshing] = useState(false);
  const [costsError, setCostsError] = useState<string | null>(null);

  const loadAlerts = useCallback(async () => {
    try {
      setAlertsError(null);
      const data = await getAlerts();
      setAlerts(data);
    } catch (err) {
      setAlertsError(err instanceof Error ? err.message : 'Failed to load compliance alerts');
    } finally {
      setAlertsLoading(false);
      setAlertsRefreshing(false);
    }
  }, []);

  const loadDeadlines = useCallback(async () => {
    try {
      setDeadlinesError(null);
      const data = await getDeadlines();
      setDeadlines(data);
    } catch (err) {
      setDeadlinesError(err instanceof Error ? err.message : 'Failed to load deadlines');
    } finally {
      setDeadlinesLoading(false);
      setDeadlinesRefreshing(false);
    }
  }, []);

  const loadCosts = useCallback(async () => {
    try {
      setCostsError(null);
      const data = await getDigest();
      setLicensesCost(data.cost_estimate_q.licenses);
      setBrandRegCost(data.cost_estimate_q.brand_registrations);
      setGrandTotal(data.cost_estimate_q.grand_total);
    } catch (err) {
      setCostsError(err instanceof Error ? err.message : 'Failed to load cost estimates');
    } finally {
      setCostsLoading(false);
      setCostsRefreshing(false);
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const data = await getComplianceStatus();
      setOverallStatus(data.overall);
    } catch {
      // Status is non-critical; silently ignore
    }
  }, []);

  useEffect(() => {
    Promise.all([loadAlerts(), loadDeadlines(), loadCosts(), loadStatus()]);
  }, [loadAlerts, loadDeadlines, loadCosts, loadStatus]);

  const criticalCount = alerts.filter((a) => a.priority === 'critical').length;
  const warningCount  = alerts.filter((a) => a.priority === 'warning').length;

  const statusCfg = overallStatus ? STATUS_CONFIG[overallStatus] : null;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Compliance Manager</Text>
        <View style={styles.alertRow}>
          {statusCfg && (
            <View style={[styles.alertChip, { backgroundColor: statusCfg.bg }]}>
              <Text style={[styles.alertChipText, { color: statusCfg.color }]}>
                {statusCfg.label}
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
      {activeTab === 'Alerts' && (
        <AlertsTab
          alerts={alerts}
          loading={alertsLoading}
          refreshing={alertsRefreshing}
          onRefresh={() => { setAlertsRefreshing(true); loadAlerts(); }}
          error={alertsError}
          criticalCount={criticalCount}
          warningCount={warningCount}
        />
      )}
      {activeTab === 'Deadlines' && (
        <DeadlinesTab
          deadlines={deadlines}
          loading={deadlinesLoading}
          refreshing={deadlinesRefreshing}
          onRefresh={() => { setDeadlinesRefreshing(true); loadDeadlines(); }}
          error={deadlinesError}
        />
      )}
      {activeTab === 'Costs' && (
        <CostsTab
          licensesCost={licensesCost}
          brandRegCost={brandRegCost}
          grandTotal={grandTotal}
          loading={costsLoading}
          refreshing={costsRefreshing}
          onRefresh={() => { setCostsRefreshing(true); loadCosts(); }}
          error={costsError}
        />
      )}
    </SafeAreaView>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  centered:  { flex: 1, justifyContent: 'center', alignItems: 'center' },

  // Header
  header:        { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  title:         { fontSize: 22, fontWeight: '800', color: COLORS.text },
  alertRow:      { flexDirection: 'row', gap: 6, marginTop: 4 },
  alertChip:     { flexDirection: 'row', alignItems: 'center', borderRadius: 99, paddingHorizontal: 10, paddingVertical: 2, gap: 4 },
  alertChipText: { fontSize: 12, fontWeight: '700' },

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

  // Chips row (alerts summary)
  chipRow: { flexDirection: 'row', gap: 8, marginBottom: 12 },

  // Alert card with priority bar
  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    marginBottom: 10,
    overflow: 'hidden',
    flexDirection: 'row',
    ...SHADOW.sm,
  },
  cardCritical: {
    backgroundColor: '#fff5f5',
  },
  priorityBar: {
    width: 4,
  },
  cardInner: {
    flex: 1,
    padding: 14,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  cardTitle:    { fontSize: 15, fontWeight: '600', color: COLORS.text, flex: 1, marginRight: 8 },
  cardSubtitle: { fontSize: 12, color: COLORS.textMuted, marginTop: 1 },

  alertMessage: {
    fontSize: 13,
    color: COLORS.textSecondary,
    marginBottom: 10,
    lineHeight: 18,
  },
  actionText: {
    fontSize: 12,
    color: COLORS.textMuted,
    fontStyle: 'italic',
    marginTop: 6,
  },

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

  // State chip
  stateChip: {
    borderRadius: RADIUS.sm,
    paddingHorizontal: 8,
    paddingVertical: 4,
  },
  stateChipText: { fontSize: 12, fontWeight: '700' },

  // Stats row
  statsRow:  { flexDirection: 'row', gap: 8 },
  stat:      { flex: 1 },
  statLabel: { fontSize: 11, color: COLORS.textMuted, marginBottom: 2 },
  statValue: { fontSize: 13, fontWeight: '600', color: COLORS.text },

  // Cost rows
  costRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
  },
  costAmount: {
    fontSize: 16,
    fontWeight: '700',
    color: COLORS.text,
  },
  divider: { height: 1, backgroundColor: COLORS.border },

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

  // Note card
  noteCard: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    borderRadius: RADIUS.md,
    padding: 12,
    marginBottom: 12,
  },
  noteText: { flex: 1, fontSize: 12, lineHeight: 17 },

  // Primary button
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: COLORS.primary,
    borderRadius: RADIUS.md,
    paddingVertical: 14,
    marginBottom: 12,
  },
  primaryButtonText: { fontSize: 15, fontWeight: '700', color: '#fff' },

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
