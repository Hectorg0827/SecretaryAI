import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import {
  getDashboardSummary,
  getSalesData,
  getEmails,
  getNotes,
  DashboardSummary,
  SalesData,
  Email,
  FollowUpNote,
  markEmailRead,
  updateNote,
} from '../api/dashboard';
import { useAuthStore } from '../stores/authStore';
import { COLORS, RADIUS, SHADOW } from '../theme';
import StatStrip from '../components/dashboard/StatStrip';
import EmailList from '../components/dashboard/EmailList';
import NotesList from '../components/dashboard/NotesList';

export default function DashboardScreen() {
  const { user } = useAuthStore();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [sales, setSales] = useState<SalesData | null>(null);
  const [emails, setEmails] = useState<Email[]>([]);
  const [notes, setNotes] = useState<FollowUpNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [s, sl, em, no] = await Promise.all([
        getDashboardSummary(),
        getSalesData(30),
        getEmails(),
        getNotes(),
      ]);
      setSummary(s);
      setSales(sl);
      setEmails(em.emails);
      setNotes(no.notes);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const handleMarkRead = async (emailId: string) => {
    await markEmailRead(emailId);
    setEmails((prev) => prev.map((e) => (e.id === emailId ? { ...e, is_read: true } : e)));
  };

  const handleToggleNote = async (noteId: string, done: boolean) => {
    await updateNote(noteId, done);
    setNotes((prev) => prev.map((n) => (n.id === noteId ? { ...n, done } : n)));
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={COLORS.primary} />
        </View>
      </SafeAreaView>
    );
  }

  const greeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={COLORS.primary} />}
      >
        {/* Header */}
        <View style={styles.header}>
          <View>
            <Text style={styles.greeting}>{greeting()}, {user?.name?.split(' ')[0] ?? 'there'}</Text>
            <Text style={styles.date}>{new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}</Text>
          </View>
          <View style={styles.avatarBox}>
            <Text style={styles.avatarLetter}>{(user?.name ?? 'U')[0].toUpperCase()}</Text>
          </View>
        </View>

        {error ? (
          <View style={styles.errorBox}>
            <Ionicons name="alert-circle" size={16} color={COLORS.danger} />
            <Text style={styles.errorText}>{error}</Text>
          </View>
        ) : null}

        {/* KPI Strip */}
        {summary && sales ? (
          <StatStrip summary={summary} sales={sales} />
        ) : null}

        {/* Revenue card */}
        {sales ? (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>Revenue (30 days)</Text>
            <Text style={styles.revenueAmount}>${sales.total_revenue.toLocaleString()}</Text>
            <View style={styles.revenueRow}>
              <Text style={styles.revenueSubtext}>{sales.order_count} orders · avg ${Math.round(sales.avg_order_value).toLocaleString()}</Text>
              {sales.vs_prior_period_pct !== null ? (
                <View style={[
                  styles.changeBadge,
                  { backgroundColor: sales.vs_prior_period_pct >= 0 ? COLORS.successLight : COLORS.dangerLight }
                ]}>
                  <Ionicons
                    name={sales.vs_prior_period_pct >= 0 ? 'arrow-up' : 'arrow-down'}
                    size={11}
                    color={sales.vs_prior_period_pct >= 0 ? COLORS.success : COLORS.danger}
                  />
                  <Text style={[
                    styles.changeBadgeText,
                    { color: sales.vs_prior_period_pct >= 0 ? COLORS.success : COLORS.danger }
                  ]}>
                    {Math.abs(sales.vs_prior_period_pct).toFixed(1)}%
                  </Text>
                </View>
              ) : null}
            </View>
          </View>
        ) : null}

        {/* Emails */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Priority Inbox</Text>
          {summary?.unread_emails ? (
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{summary.unread_emails}</Text>
            </View>
          ) : null}
        </View>
        <EmailList emails={emails.slice(0, 5)} onMarkRead={handleMarkRead} />

        {/* Follow-ups */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Follow-ups</Text>
        </View>
        <NotesList notes={notes} onToggle={handleToggleNote} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  content: { paddingHorizontal: 16, paddingBottom: 24 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 16,
  },
  greeting: { fontSize: 20, fontWeight: '700', color: COLORS.text },
  date: { fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  avatarBox: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  avatarLetter: { color: '#fff', fontWeight: '700', fontSize: 16 },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.dangerLight,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 12,
    gap: 6,
  },
  errorText: { color: COLORS.danger, fontSize: 13, flex: 1 },
  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 16,
    marginBottom: 12,
    ...SHADOW.sm,
  },
  cardTitle: { fontSize: 13, color: COLORS.textSecondary, fontWeight: '600', marginBottom: 4 },
  revenueAmount: { fontSize: 28, fontWeight: '800', color: COLORS.text },
  revenueRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 },
  revenueSubtext: { fontSize: 13, color: COLORS.textSecondary },
  changeBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 99,
    paddingHorizontal: 8,
    paddingVertical: 2,
    gap: 2,
  },
  changeBadgeText: { fontSize: 12, fontWeight: '600' },
  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 4,
    marginBottom: 8,
    gap: 8,
  },
  sectionTitle: { fontSize: 16, fontWeight: '700', color: COLORS.text },
  badge: {
    backgroundColor: COLORS.primary,
    borderRadius: 99,
    paddingHorizontal: 8,
    paddingVertical: 1,
  },
  badgeText: { color: '#fff', fontSize: 12, fontWeight: '600' },
});
