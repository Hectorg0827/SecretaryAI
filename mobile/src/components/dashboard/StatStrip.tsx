import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { DashboardSummary, SalesData } from '../../api/dashboard';
import { COLORS, RADIUS, SHADOW } from '../../theme';

interface Props {
  summary: DashboardSummary;
  sales: SalesData;
}

interface StatCardProps {
  icon: keyof typeof Ionicons.glyphMap;
  iconColor: string;
  iconBg: string;
  label: string;
  value: string | number;
  sub?: string;
}

function StatCard({ icon, iconColor, iconBg, label, value, sub }: StatCardProps) {
  return (
    <View style={styles.card}>
      <View style={[styles.iconBox, { backgroundColor: iconBg }]}>
        <Ionicons name={icon} size={20} color={iconColor} />
      </View>
      <Text style={styles.value}>{value}</Text>
      <Text style={styles.label}>{label}</Text>
      {sub ? <Text style={styles.sub}>{sub}</Text> : null}
    </View>
  );
}

export default function StatStrip({ summary, sales }: Props) {
  return (
    <View style={styles.strip}>
      <StatCard
        icon="people"
        iconColor={COLORS.primary}
        iconBg={COLORS.primaryLight}
        label="Accounts"
        value={summary.accounts.total}
        sub={summary.accounts.at_risk > 0 ? `${summary.accounts.at_risk} at risk` : undefined}
      />
      <StatCard
        icon="mail"
        iconColor={COLORS.warning}
        iconBg={COLORS.warningLight}
        label="Emails"
        value={summary.unread_emails}
        sub="unread"
      />
      <StatCard
        icon="alert-circle"
        iconColor={COLORS.danger}
        iconBg={COLORS.dangerLight}
        label="Alerts"
        value={summary.inventory_alerts.length}
        sub="inventory"
      />
      <StatCard
        icon="checkmark-circle"
        iconColor={COLORS.success}
        iconBg={COLORS.successLight}
        label="Approvals"
        value={summary.pending_actions}
        sub="pending"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  strip: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 12,
  },
  card: {
    flex: 1,
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.md,
    padding: 10,
    alignItems: 'center',
    ...SHADOW.sm,
  },
  iconBox: {
    width: 36,
    height: 36,
    borderRadius: RADIUS.sm,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 6,
  },
  value: {
    fontSize: 18,
    fontWeight: '800',
    color: COLORS.text,
  },
  label: {
    fontSize: 10,
    color: COLORS.textSecondary,
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  sub: {
    fontSize: 10,
    color: COLORS.textMuted,
    marginTop: 1,
  },
});
