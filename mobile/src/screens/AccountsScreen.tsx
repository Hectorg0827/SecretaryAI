import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  TextInput,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { NativeStackScreenProps } from '@react-navigation/native-stack';
import { getAccounts, Account } from '../api/accounts';
import { AccountsStackParamList } from '../navigation/types';
import { COLORS, RADIUS, SHADOW } from '../theme';

type Props = NativeStackScreenProps<AccountsStackParamList, 'AccountsList'>;

const HEALTH_CONFIG = {
  healthy: { label: 'Healthy', color: COLORS.success, bg: COLORS.successLight },
  at_risk: { label: 'At Risk', color: COLORS.warning, bg: COLORS.warningLight },
  churned: { label: 'Churned', color: COLORS.danger, bg: COLORS.dangerLight },
  new: { label: 'New', color: COLORS.primary, bg: COLORS.primaryLight },
};

function AccountCard({ account, onPress }: { account: Account; onPress: () => void }) {
  const health = HEALTH_CONFIG[account.health_status] ?? HEALTH_CONFIG.healthy;
  return (
    <TouchableOpacity style={styles.card} onPress={onPress} activeOpacity={0.85}>
      <View style={styles.cardRow}>
        <View style={styles.cardLeft}>
          <View style={[styles.dot, { backgroundColor: health.color }]} />
          <Text style={styles.accountName} numberOfLines={1}>{account.name}</Text>
        </View>
        <View style={[styles.healthBadge, { backgroundColor: health.bg }]}>
          <Text style={[styles.healthBadgeText, { color: health.color }]}>{health.label}</Text>
        </View>
      </View>
      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Balance</Text>
          <Text style={styles.statValue}>${account.current_balance.toLocaleString()}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Avg Order</Text>
          <Text style={styles.statValue}>${Math.round(account.avg_order_value).toLocaleString()}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Last Order</Text>
          <Text style={styles.statValue}>
            {account.last_order_date
              ? new Date(account.last_order_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
              : '—'}
          </Text>
        </View>
      </View>
      {account.assigned_rep ? (
        <Text style={styles.rep}>Rep: {account.assigned_rep}</Text>
      ) : null}
    </TouchableOpacity>
  );
}

export default function AccountsScreen({ navigation }: Props) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [filtered, setFiltered] = useState<Account[]>([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await getAccounts();
      setAccounts(data.accounts);
      setFiltered(data.accounts);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load accounts');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let result = accounts;
    if (filter) result = result.filter((a) => a.health_status === filter);
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((a) => a.name.toLowerCase().includes(q) || (a.email ?? '').toLowerCase().includes(q));
    }
    setFiltered(result);
  }, [search, filter, accounts]);

  if (loading) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={COLORS.primary} />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Accounts</Text>
        <Text style={styles.subtitle}>{filtered.length} customers</Text>
      </View>

      {/* Search */}
      <View style={styles.searchRow}>
        <Ionicons name="search" size={16} color={COLORS.textMuted} style={styles.searchIcon} />
        <TextInput
          style={styles.searchInput}
          value={search}
          onChangeText={setSearch}
          placeholder="Search accounts…"
          placeholderTextColor={COLORS.textMuted}
          autoCorrect={false}
        />
        {search ? (
          <TouchableOpacity onPress={() => setSearch('')}>
            <Ionicons name="close-circle" size={16} color={COLORS.textMuted} />
          </TouchableOpacity>
        ) : null}
      </View>

      {/* Filter chips */}
      <View style={styles.filterRow}>
        {[null, 'at_risk', 'healthy', 'churned', 'new'].map((f) => (
          <TouchableOpacity
            key={String(f)}
            style={[styles.chip, filter === f && styles.chipActive]}
            onPress={() => setFilter(f)}
          >
            <Text style={[styles.chipText, filter === f && styles.chipTextActive]}>
              {f === null ? 'All' : HEALTH_CONFIG[f as keyof typeof HEALTH_CONFIG]?.label ?? f}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      <FlatList
        data={filtered}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <AccountCard
            account={item}
            onPress={() => navigation.navigate('AccountDetail', { accountId: item.id, accountName: item.name })}
          />
        )}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={COLORS.primary} />}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="people-outline" size={40} color={COLORS.textMuted} />
            <Text style={styles.emptyText}>No accounts found</Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: { paddingHorizontal: 16, paddingTop: 16, paddingBottom: 8 },
  title: { fontSize: 22, fontWeight: '800', color: COLORS.text },
  subtitle: { fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  searchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.md,
    marginHorizontal: 16,
    marginBottom: 8,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  searchIcon: { marginRight: 6 },
  searchInput: { flex: 1, paddingVertical: 10, fontSize: 14, color: COLORS.text },
  filterRow: { flexDirection: 'row', paddingHorizontal: 16, marginBottom: 8, gap: 6, flexWrap: 'wrap' },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 99,
    backgroundColor: COLORS.surface,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  chipActive: { backgroundColor: COLORS.primary, borderColor: COLORS.primary },
  chipText: { fontSize: 13, color: COLORS.textSecondary, fontWeight: '500' },
  chipTextActive: { color: '#fff' },
  list: { paddingHorizontal: 16, paddingBottom: 24 },
  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 14,
    marginBottom: 10,
    ...SHADOW.sm,
  },
  cardRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  cardLeft: { flexDirection: 'row', alignItems: 'center', flex: 1, gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  accountName: { fontSize: 15, fontWeight: '600', color: COLORS.text, flex: 1 },
  healthBadge: { borderRadius: 99, paddingHorizontal: 10, paddingVertical: 3 },
  healthBadgeText: { fontSize: 11, fontWeight: '700' },
  statsRow: { flexDirection: 'row', gap: 8 },
  stat: { flex: 1 },
  statLabel: { fontSize: 11, color: COLORS.textMuted, marginBottom: 2 },
  statValue: { fontSize: 13, fontWeight: '600', color: COLORS.text },
  rep: { fontSize: 12, color: COLORS.textMuted, marginTop: 8 },
  errorBox: {
    backgroundColor: COLORS.dangerLight,
    marginHorizontal: 16,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 8,
  },
  errorText: { color: COLORS.danger, fontSize: 13 },
  emptyState: { alignItems: 'center', paddingTop: 60, gap: 8 },
  emptyText: { color: COLORS.textMuted, fontSize: 15 },
});
