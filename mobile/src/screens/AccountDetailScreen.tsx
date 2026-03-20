import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { NativeStackScreenProps } from '@react-navigation/native-stack';
import { AccountsStackParamList } from '../navigation/types';
import { apiFetch } from '../api/client';
import { Account } from '../api/accounts';
import { COLORS, RADIUS, SHADOW } from '../theme';

type Props = NativeStackScreenProps<AccountsStackParamList, 'AccountDetail'>;

interface AccountDetail extends Account {
  velocity?: {
    avg_days_between_orders?: number;
    total_revenue_90d?: number;
    order_count_90d?: number;
  };
  trend_30d?: {
    change_pct?: number;
    direction?: 'up' | 'down' | 'flat';
  };
  recent_invoices?: {
    order_date: string;
    total_amount: number;
    status: string;
  }[];
}

const HEALTH_CONFIG = {
  healthy: { label: 'Healthy', color: COLORS.success, bg: COLORS.successLight },
  at_risk: { label: 'At Risk', color: COLORS.warning, bg: COLORS.warningLight },
  churned: { label: 'Churned', color: COLORS.danger, bg: COLORS.dangerLight },
  new: { label: 'New', color: COLORS.primary, bg: COLORS.primaryLight },
};

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

export default function AccountDetailScreen({ route, navigation }: Props) {
  const { accountId, accountName } = route.params;
  const [detail, setDetail] = useState<AccountDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    navigation.setOptions({ title: accountName });
    apiFetch<AccountDetail>(`/api/accounts/${accountId}`)
      .then(setDetail)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load account'))
      .finally(() => setLoading(false));
  }, [accountId]);

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}><ActivityIndicator size="large" color={COLORS.primary} /></View>
      </SafeAreaView>
    );
  }

  if (error || !detail) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <Ionicons name="alert-circle" size={40} color={COLORS.danger} />
          <Text style={styles.errorText}>{error ?? 'Account not found'}</Text>
        </View>
      </SafeAreaView>
    );
  }

  const health = HEALTH_CONFIG[detail.health_status] ?? HEALTH_CONFIG.healthy;
  const invoices = detail.recent_invoices ?? [];

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* Header card */}
        <View style={styles.headerCard}>
          <View style={styles.headerTop}>
            <View style={styles.avatar}>
              <Text style={styles.avatarLetter}>{detail.name[0]?.toUpperCase()}</Text>
            </View>
            <View style={styles.headerText}>
              <Text style={styles.accountName}>{detail.name}</Text>
              {detail.state ? <Text style={styles.location}>{detail.state}</Text> : null}
            </View>
            <View style={[styles.healthBadge, { backgroundColor: health.bg }]}>
              <Text style={[styles.healthBadgeText, { color: health.color }]}>{health.label}</Text>
            </View>
          </View>
          <View style={styles.statsGrid}>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>Balance</Text>
              <Text style={styles.statValue}>${detail.current_balance.toLocaleString()}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>Avg Order</Text>
              <Text style={styles.statValue}>${Math.round(detail.avg_order_value).toLocaleString()}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>Health Score</Text>
              <Text style={[styles.statValue, { color: health.color }]}>{detail.health_score}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>Last Order</Text>
              <Text style={styles.statValue}>
                {detail.last_order_date
                  ? new Date(detail.last_order_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                  : '—'}
              </Text>
            </View>
          </View>
        </View>

        {/* Contact info */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Contact</Text>
          {detail.email ? <InfoRow label="Email" value={detail.email} /> : null}
          {detail.phone ? <InfoRow label="Phone" value={detail.phone} /> : null}
          {detail.assigned_rep ? <InfoRow label="Sales Rep" value={detail.assigned_rep} /> : null}
          {!detail.email && !detail.phone && !detail.assigned_rep ? (
            <Text style={styles.noData}>No contact info on file</Text>
          ) : null}
        </View>

        {/* Velocity */}
        {detail.velocity && Object.keys(detail.velocity).length > 0 ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Sales Velocity (90 days)</Text>
            <View style={styles.velocityRow}>
              {detail.velocity.total_revenue_90d !== undefined ? (
                <View style={styles.velocityStat}>
                  <Text style={styles.velocityValue}>${Math.round(detail.velocity.total_revenue_90d).toLocaleString()}</Text>
                  <Text style={styles.velocityLabel}>Revenue</Text>
                </View>
              ) : null}
              {detail.velocity.order_count_90d !== undefined ? (
                <View style={styles.velocityStat}>
                  <Text style={styles.velocityValue}>{detail.velocity.order_count_90d}</Text>
                  <Text style={styles.velocityLabel}>Orders</Text>
                </View>
              ) : null}
              {detail.velocity.avg_days_between_orders !== undefined ? (
                <View style={styles.velocityStat}>
                  <Text style={styles.velocityValue}>{Math.round(detail.velocity.avg_days_between_orders)}d</Text>
                  <Text style={styles.velocityLabel}>Avg Cycle</Text>
                </View>
              ) : null}
            </View>
            {detail.trend_30d?.change_pct !== undefined ? (
              <View style={[
                styles.trendBanner,
                { backgroundColor: (detail.trend_30d.change_pct ?? 0) >= 0 ? COLORS.successLight : COLORS.dangerLight }
              ]}>
                <Ionicons
                  name={(detail.trend_30d.change_pct ?? 0) >= 0 ? 'trending-up' : 'trending-down'}
                  size={14}
                  color={(detail.trend_30d.change_pct ?? 0) >= 0 ? COLORS.success : COLORS.danger}
                />
                <Text style={[
                  styles.trendText,
                  { color: (detail.trend_30d.change_pct ?? 0) >= 0 ? COLORS.success : COLORS.danger }
                ]}>
                  {Math.abs(detail.trend_30d.change_pct ?? 0).toFixed(1)}% vs prior 30 days
                </Text>
              </View>
            ) : null}
          </View>
        ) : null}

        {/* Recent orders */}
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Recent Orders</Text>
          {invoices.length === 0 ? (
            <Text style={styles.noData}>No orders in the last 12 months</Text>
          ) : (
            invoices.map((inv, i) => (
              <View key={i} style={[styles.invoiceRow, i > 0 && styles.invoiceBorder]}>
                <Text style={styles.invoiceDate}>
                  {inv.order_date
                    ? new Date(inv.order_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                    : '—'}
                </Text>
                <Text style={[
                  styles.invoiceStatus,
                  { color: inv.status === 'paid' ? COLORS.success : COLORS.textMuted }
                ]}>
                  {inv.status}
                </Text>
                <Text style={styles.invoiceAmount}>${inv.total_amount.toLocaleString()}</Text>
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
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: 12 },
  content: { padding: 16, paddingBottom: 32 },
  headerCard: { backgroundColor: COLORS.surface, borderRadius: RADIUS.lg, padding: 16, marginBottom: 12, ...SHADOW.sm },
  headerTop: { flexDirection: 'row', alignItems: 'center', marginBottom: 16, gap: 12 },
  avatar: { width: 48, height: 48, borderRadius: 24, backgroundColor: COLORS.primary, justifyContent: 'center', alignItems: 'center' },
  avatarLetter: { color: '#fff', fontWeight: '800', fontSize: 20 },
  headerText: { flex: 1 },
  accountName: { fontSize: 18, fontWeight: '700', color: COLORS.text },
  location: { fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  healthBadge: { borderRadius: 99, paddingHorizontal: 10, paddingVertical: 4 },
  healthBadgeText: { fontSize: 12, fontWeight: '700' },
  statsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  statCell: { flex: 1, minWidth: '40%', backgroundColor: COLORS.background, borderRadius: RADIUS.sm, padding: 10 },
  statLabel: { fontSize: 11, color: COLORS.textMuted, marginBottom: 3 },
  statValue: { fontSize: 16, fontWeight: '700', color: COLORS.text },
  card: { backgroundColor: COLORS.surface, borderRadius: RADIUS.lg, padding: 16, marginBottom: 12, ...SHADOW.sm },
  cardTitle: { fontSize: 14, fontWeight: '700', color: COLORS.text, marginBottom: 12 },
  infoRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6, borderBottomWidth: 1, borderBottomColor: COLORS.border },
  infoLabel: { fontSize: 13, color: COLORS.textSecondary },
  infoValue: { fontSize: 13, color: COLORS.text, fontWeight: '500' },
  noData: { fontSize: 13, color: COLORS.textMuted, fontStyle: 'italic' },
  velocityRow: { flexDirection: 'row', gap: 8, marginBottom: 12 },
  velocityStat: { flex: 1, backgroundColor: COLORS.background, borderRadius: RADIUS.sm, padding: 10, alignItems: 'center' },
  velocityValue: { fontSize: 17, fontWeight: '800', color: COLORS.text },
  velocityLabel: { fontSize: 11, color: COLORS.textMuted, marginTop: 2 },
  trendBanner: { flexDirection: 'row', alignItems: 'center', borderRadius: RADIUS.sm, padding: 8, gap: 6 },
  trendText: { fontSize: 13, fontWeight: '600' },
  invoiceRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 9 },
  invoiceBorder: { borderTopWidth: 1, borderTopColor: COLORS.border },
  invoiceDate: { flex: 1, fontSize: 13, color: COLORS.text },
  invoiceStatus: { fontSize: 12, fontWeight: '500', textTransform: 'capitalize', marginRight: 12 },
  invoiceAmount: { fontSize: 14, fontWeight: '700', color: COLORS.text },
  errorText: { fontSize: 15, color: COLORS.danger },
});
