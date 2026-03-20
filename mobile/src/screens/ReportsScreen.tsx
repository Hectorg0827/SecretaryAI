import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  RefreshControl,
  ActivityIndicator,
  TouchableOpacity,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { getSalesData, SalesData } from '../api/dashboard';
import { getAccounts, Account } from '../api/accounts';
import { getInventoryAlerts, InventoryItem } from '../api/inventory';
import { COLORS, RADIUS, SHADOW } from '../theme';

type Period = 30 | 60 | 90;

function BarRow({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.max(4, (value / max) * 100) : 0;
  return (
    <View style={barStyles.row}>
      <Text style={barStyles.label} numberOfLines={1}>{label}</Text>
      <View style={barStyles.track}>
        <View style={[barStyles.fill, { width: `${pct}%` as any, backgroundColor: color }]} />
      </View>
      <Text style={barStyles.value}>${value >= 1000 ? `${(value / 1000).toFixed(1)}k` : value.toFixed(0)}</Text>
    </View>
  );
}

const barStyles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', marginBottom: 10, gap: 8 },
  label: { width: 90, fontSize: 12, color: COLORS.textSecondary },
  track: { flex: 1, height: 10, backgroundColor: COLORS.border, borderRadius: 5, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 5 },
  value: { width: 50, fontSize: 12, fontWeight: '600', color: COLORS.text, textAlign: 'right' },
});

export default function ReportsScreen() {
  const [period, setPeriod] = useState<Period>(30);
  const [sales, setSales] = useState<SalesData | null>(null);
  const [prevSales, setPrevSales] = useState<SalesData | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [alerts, setAlerts] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (p: Period = period) => {
    try {
      setError(null);
      const [s, pS, acc, inv] = await Promise.all([
        getSalesData(p),
        getSalesData(p * 2),
        getAccounts(),
        getInventoryAlerts(),
      ]);
      setSales(s);
      setPrevSales(pS);
      setAccounts(acc.accounts);
      setAlerts(inv.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load reports');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [period]);

  useEffect(() => {
    setLoading(true);
    load(period);
  }, [period]);

  const handleRefresh = () => {
    setRefreshing(true);
    load(period);
  };

  // Compute top accounts by total spend from accounts list
  const topAccounts = [...accounts]
    .sort((a, b) => b.avg_order_value - a.avg_order_value)
    .slice(0, 6);
  const maxAvg = topAccounts[0]?.avg_order_value ?? 1;

  // Health breakdown
  const healthy = accounts.filter((a) => a.health_status === 'healthy').length;
  const atRisk = accounts.filter((a) => a.health_status === 'at_risk').length;
  const churned = accounts.filter((a) => a.health_status === 'churned').length;
  const total = accounts.length || 1;

  // Revenue change
  const periodRevenue = sales?.total_revenue ?? 0;
  const prevHalfRevenue = prevSales ? prevSales.total_revenue - periodRevenue : 0;
  const revenueChange = prevHalfRevenue > 0 ? ((periodRevenue - prevHalfRevenue) / prevHalfRevenue) * 100 : null;

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}><ActivityIndicator size="large" color={COLORS.primary} /></View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={handleRefresh} tintColor={COLORS.primary} />}
      >
        <View style={styles.header}>
          <Text style={styles.title}>Reports</Text>
          <View style={styles.periodRow}>
            {([30, 60, 90] as Period[]).map((p) => (
              <TouchableOpacity
                key={p}
                style={[styles.periodChip, period === p && styles.periodChipActive]}
                onPress={() => setPeriod(p)}
              >
                <Text style={[styles.periodText, period === p && styles.periodTextActive]}>{p}d</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {error ? (
          <View style={styles.errorBox}>
            <Text style={styles.errorText}>{error}</Text>
          </View>
        ) : null}

        {/* Revenue summary */}
        {sales ? (
          <View style={styles.card}>
            <View style={styles.cardHeaderRow}>
              <Text style={styles.cardTitle}>Revenue — last {period} days</Text>
              {revenueChange !== null ? (
                <View style={[styles.changeBadge, { backgroundColor: revenueChange >= 0 ? COLORS.successLight : COLORS.dangerLight }]}>
                  <Ionicons
                    name={revenueChange >= 0 ? 'trending-up' : 'trending-down'}
                    size={12}
                    color={revenueChange >= 0 ? COLORS.success : COLORS.danger}
                  />
                  <Text style={[styles.changeText, { color: revenueChange >= 0 ? COLORS.success : COLORS.danger }]}>
                    {Math.abs(revenueChange).toFixed(1)}% vs prior
                  </Text>
                </View>
              ) : null}
            </View>
            <Text style={styles.bigNumber}>${sales.total_revenue.toLocaleString()}</Text>
            <View style={styles.metaRow}>
              <View style={styles.metaItem}>
                <Text style={styles.metaLabel}>Orders</Text>
                <Text style={styles.metaValue}>{sales.order_count}</Text>
              </View>
              <View style={styles.metaDivider} />
              <View style={styles.metaItem}>
                <Text style={styles.metaLabel}>Avg Order</Text>
                <Text style={styles.metaValue}>${Math.round(sales.avg_order_value).toLocaleString()}</Text>
              </View>
              {sales.top_account ? (
                <>
                  <View style={styles.metaDivider} />
                  <View style={styles.metaItem}>
                    <Text style={styles.metaLabel}>Top Account</Text>
                    <Text style={styles.metaValue} numberOfLines={1}>{sales.top_account}</Text>
                  </View>
                </>
              ) : null}
            </View>
          </View>
        ) : null}

        {/* Account health */}
        {accounts.length > 0 ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Account Health</Text>
            <Text style={styles.cardSubtitle}>{accounts.length} total customers</Text>
            <View style={styles.healthBars}>
              {[
                { label: 'Healthy', count: healthy, color: COLORS.success, bg: COLORS.successLight },
                { label: 'At Risk', count: atRisk, color: COLORS.warning, bg: COLORS.warningLight },
                { label: 'Churned', count: churned, color: COLORS.danger, bg: COLORS.dangerLight },
              ].map(({ label, count, color, bg }) => (
                <View key={label} style={styles.healthRow}>
                  <View style={[styles.healthDot, { backgroundColor: color }]} />
                  <Text style={styles.healthLabel}>{label}</Text>
                  <View style={styles.healthTrack}>
                    <View style={[styles.healthFill, { width: `${(count / total) * 100}%` as any, backgroundColor: color }]} />
                  </View>
                  <Text style={styles.healthCount}>{count}</Text>
                  <Text style={styles.healthPct}>{total > 0 ? Math.round((count / total) * 100) : 0}%</Text>
                </View>
              ))}
            </View>
          </View>
        ) : null}

        {/* Top accounts by avg order value */}
        {topAccounts.length > 0 ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Top Accounts by Avg Order</Text>
            <View style={styles.barsContainer}>
              {topAccounts.map((a) => (
                <BarRow
                  key={a.id}
                  label={a.name}
                  value={a.avg_order_value}
                  max={maxAvg}
                  color={COLORS.primary}
                />
              ))}
            </View>
          </View>
        ) : null}

        {/* Inventory status */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Inventory Alerts</Text>
          {alerts.length === 0 ? (
            <View style={styles.noAlerts}>
              <Ionicons name="checkmark-circle" size={24} color={COLORS.success} />
              <Text style={styles.noAlertsText}>All stock levels healthy</Text>
            </View>
          ) : (
            alerts.slice(0, 6).map((item) => (
              <View key={item.item_id} style={styles.alertRow}>
                <View style={[
                  styles.alertDot,
                  { backgroundColor: item.stock_status === 'critical' ? COLORS.danger : COLORS.warning }
                ]} />
                <Text style={styles.alertName} numberOfLines={1}>{item.product_name}</Text>
                <Text style={[
                  styles.alertStatus,
                  { color: item.stock_status === 'critical' ? COLORS.danger : COLORS.warning }
                ]}>
                  {item.stock_status === 'critical' ? 'Critical' : 'Low'}
                </Text>
                <Text style={styles.alertQty}>{item.total_qty} units</Text>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  content: { paddingHorizontal: 16, paddingBottom: 24 },
  header: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 16 },
  title: { fontSize: 22, fontWeight: '800', color: COLORS.text },
  periodRow: { flexDirection: 'row', gap: 6 },
  periodChip: {
    paddingHorizontal: 12, paddingVertical: 5, borderRadius: 99,
    backgroundColor: COLORS.surface, borderWidth: 1, borderColor: COLORS.border,
  },
  periodChipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  periodText: { fontSize: 13, color: COLORS.textSecondary, fontWeight: '500' },
  periodTextActive: { color: '#fff' },
  errorBox: { backgroundColor: COLORS.dangerLight, borderRadius: RADIUS.sm, padding: 10, marginBottom: 12 },
  errorText: { color: COLORS.danger, fontSize: 13 },
  card: { backgroundColor: COLORS.surface, borderRadius: RADIUS.lg, padding: 16, marginBottom: 12, ...SHADOW.sm },
  cardHeaderRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 },
  cardTitle: { fontSize: 14, fontWeight: '700', color: COLORS.text },
  cardSubtitle: { fontSize: 12, color: COLORS.textMuted, marginBottom: 12, marginTop: 2 },
  bigNumber: { fontSize: 32, fontWeight: '800', color: COLORS.text, marginBottom: 12 },
  changeBadge: { flexDirection: 'row', alignItems: 'center', borderRadius: 99, paddingHorizontal: 8, paddingVertical: 2, gap: 3 },
  changeText: { fontSize: 11, fontWeight: '600' },
  metaRow: { flexDirection: 'row', alignItems: 'center' },
  metaItem: { flex: 1 },
  metaLabel: { fontSize: 11, color: COLORS.textMuted },
  metaValue: { fontSize: 14, fontWeight: '700', color: COLORS.text },
  metaDivider: { width: 1, height: 28, backgroundColor: COLORS.border, marginHorizontal: 8 },
  healthBars: { marginTop: 8, gap: 10 },
  healthRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  healthDot: { width: 8, height: 8, borderRadius: 4 },
  healthLabel: { width: 54, fontSize: 12, color: COLORS.textSecondary },
  healthTrack: { flex: 1, height: 8, backgroundColor: COLORS.border, borderRadius: 4, overflow: 'hidden' },
  healthFill: { height: '100%', borderRadius: 4 },
  healthCount: { width: 24, fontSize: 12, fontWeight: '700', color: COLORS.text, textAlign: 'right' },
  healthPct: { width: 34, fontSize: 11, color: COLORS.textMuted, textAlign: 'right' },
  barsContainer: { marginTop: 10 },
  noAlerts: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  noAlertsText: { fontSize: 14, color: COLORS.success, fontWeight: '500' },
  alertRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 8, borderTopWidth: 1, borderTopColor: COLORS.border, gap: 8 },
  alertDot: { width: 8, height: 8, borderRadius: 4 },
  alertName: { flex: 1, fontSize: 13, color: COLORS.text },
  alertStatus: { fontSize: 12, fontWeight: '700' },
  alertQty: { fontSize: 12, color: COLORS.textMuted, width: 56, textAlign: 'right' },
});
