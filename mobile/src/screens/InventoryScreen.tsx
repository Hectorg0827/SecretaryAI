import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { getInventory, InventoryItem } from '../api/inventory';
import { COLORS, RADIUS, SHADOW } from '../theme';

const STATUS_CONFIG = {
  critical: { label: 'Critical', color: COLORS.danger, bg: COLORS.dangerLight, icon: 'alert-circle' as const },
  low: { label: 'Low', color: COLORS.warning, bg: COLORS.warningLight, icon: 'warning' as const },
  healthy: { label: 'OK', color: COLORS.success, bg: COLORS.successLight, icon: 'checkmark-circle' as const },
};

function InventoryCard({ item }: { item: InventoryItem }) {
  const config = STATUS_CONFIG[item.stock_status];
  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={styles.cardTitleRow}>
          <Text style={styles.productName} numberOfLines={1}>{item.product_name}</Text>
          {item.sku ? <Text style={styles.sku}>{item.sku}</Text> : null}
        </View>
        <View style={[styles.statusBadge, { backgroundColor: config.bg }]}>
          <Ionicons name={config.icon} size={12} color={config.color} />
          <Text style={[styles.statusText, { color: config.color }]}>{config.label}</Text>
        </View>
      </View>

      <View style={styles.statsRow}>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>On Hand</Text>
          <Text style={styles.statValue}>{item.total_qty.toLocaleString()}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>QB Qty</Text>
          <Text style={styles.statValue}>{item.qb_qty.toLocaleString()}</Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Weeks Left</Text>
          <Text style={[styles.statValue, item.stock_status === 'critical' && { color: COLORS.danger }]}>
            {item.weeks_remaining != null ? `${item.weeks_remaining.toFixed(1)}w` : '—'}
          </Text>
        </View>
        <View style={styles.stat}>
          <Text style={styles.statLabel}>Sell Rate</Text>
          <Text style={styles.statValue}>
            {item.weekly_sell_rate != null ? `${item.weekly_sell_rate.toFixed(1)}/wk` : '—'}
          </Text>
        </View>
      </View>

      {item.needs_po ? (
        <View style={styles.poBanner}>
          <Ionicons name="cart" size={13} color={COLORS.warning} />
          <Text style={styles.poBannerText}>Purchase order recommended</Text>
        </View>
      ) : null}
    </View>
  );
}

export default function InventoryScreen() {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [filtered, setFiltered] = useState<InventoryItem[]>([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'critical' | 'low' | 'healthy' | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await getInventory();
      setItems(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load inventory');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let result = items;
    if (filter) result = result.filter((i) => i.stock_status === filter);
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter((i) => i.product_name.toLowerCase().includes(q) || (i.sku ?? '').toLowerCase().includes(q));
    }
    // Sort: critical first, then low, then healthy
    result = [...result].sort((a, b) => {
      const order = { critical: 0, low: 1, healthy: 2 };
      return order[a.stock_status] - order[b.stock_status];
    });
    setFiltered(result);
  }, [search, filter, items]);

  const criticalCount = items.filter((i) => i.stock_status === 'critical').length;
  const lowCount = items.filter((i) => i.stock_status === 'low').length;

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
        <Text style={styles.title}>Inventory</Text>
        {(criticalCount > 0 || lowCount > 0) ? (
          <View style={styles.alertRow}>
            {criticalCount > 0 && (
              <View style={[styles.alertChip, { backgroundColor: COLORS.dangerLight }]}>
                <Text style={[styles.alertChipText, { color: COLORS.danger }]}>
                  {criticalCount} critical
                </Text>
              </View>
            )}
            {lowCount > 0 && (
              <View style={[styles.alertChip, { backgroundColor: COLORS.warningLight }]}>
                <Text style={[styles.alertChipText, { color: COLORS.warning }]}>
                  {lowCount} low
                </Text>
              </View>
            )}
          </View>
        ) : null}
      </View>

      {/* Search */}
      <View style={styles.searchRow}>
        <Ionicons name="search" size={16} color={COLORS.textMuted} style={styles.searchIcon} />
        <TextInput
          style={styles.searchInput}
          value={search}
          onChangeText={setSearch}
          placeholder="Search products…"
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
        {([null, 'critical', 'low', 'healthy'] as const).map((f) => (
          <TouchableOpacity
            key={String(f)}
            style={[styles.chip, filter === f && styles.chipActive]}
            onPress={() => setFilter(f)}
          >
            <Text style={[styles.chipText, filter === f && styles.chipTextActive]}>
              {f === null ? 'All' : STATUS_CONFIG[f].label}
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
        keyExtractor={(item) => item.item_id}
        renderItem={({ item }) => <InventoryCard item={item} />}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={COLORS.primary} />}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="cube-outline" size={40} color={COLORS.textMuted} />
            <Text style={styles.emptyText}>No items found</Text>
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
  alertRow: { flexDirection: 'row', gap: 6, marginTop: 4 },
  alertChip: { borderRadius: 99, paddingHorizontal: 10, paddingVertical: 2 },
  alertChipText: { fontSize: 12, fontWeight: '700' },
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
  filterRow: { flexDirection: 'row', paddingHorizontal: 16, marginBottom: 8, gap: 6 },
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
  cardHeader: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 },
  cardTitleRow: { flex: 1, marginRight: 8 },
  productName: { fontSize: 15, fontWeight: '600', color: COLORS.text },
  sku: { fontSize: 11, color: COLORS.textMuted, marginTop: 1 },
  statusBadge: { flexDirection: 'row', alignItems: 'center', borderRadius: 99, paddingHorizontal: 8, paddingVertical: 3, gap: 3 },
  statusText: { fontSize: 11, fontWeight: '700' },
  statsRow: { flexDirection: 'row', gap: 8 },
  stat: { flex: 1 },
  statLabel: { fontSize: 11, color: COLORS.textMuted, marginBottom: 2 },
  statValue: { fontSize: 13, fontWeight: '600', color: COLORS.text },
  poBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginTop: 10,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  poBannerText: { fontSize: 12, color: COLORS.warning, fontWeight: '500' },
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
