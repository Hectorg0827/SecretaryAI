import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  FlatList,
  StyleSheet,
  RefreshControl,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Modal,
  TextInput,
  ScrollView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { getPendingActions, approveAction, rejectAction, Draft } from '../api/actions';
import { COLORS, RADIUS, SHADOW } from '../theme';

const ACTION_LABELS: Record<string, string> = {
  draft_email: 'Draft Email',
  draft_po: 'Purchase Order',
  send_report: 'Send Report',
  update_record: 'Update Record',
};

function DraftCard({
  draft,
  onApprove,
  onReject,
}: {
  draft: Draft;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const label = ACTION_LABELS[draft.action_type] ?? draft.action_type;
  const preview = typeof draft.content === 'object'
    ? JSON.stringify(draft.content, null, 2)
    : String(draft.content);

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View style={styles.cardTypeTag}>
          <Text style={styles.cardTypeText}>{label}</Text>
        </View>
        <Text style={styles.cardDate}>
          {new Date(draft.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
        </Text>
      </View>

      {/* Content preview */}
      {draft.content && typeof draft.content === 'object' ? (
        <View style={styles.contentBox}>
          {Object.entries(draft.content).slice(0, expanded ? undefined : 3).map(([k, v]) => (
            <View key={k} style={styles.contentRow}>
              <Text style={styles.contentKey}>{k}</Text>
              <Text style={styles.contentValue} numberOfLines={expanded ? undefined : 2}>
                {String(v)}
              </Text>
            </View>
          ))}
          {Object.keys(draft.content).length > 3 && (
            <TouchableOpacity onPress={() => setExpanded(!expanded)}>
              <Text style={styles.expandBtn}>{expanded ? 'Show less' : `+${Object.keys(draft.content).length - 3} more fields`}</Text>
            </TouchableOpacity>
          )}
        </View>
      ) : null}

      {/* Actions */}
      <View style={styles.actionRow}>
        <TouchableOpacity
          style={[styles.actionBtn, styles.rejectBtn]}
          onPress={() => onReject(draft.id)}
        >
          <Ionicons name="close" size={16} color={COLORS.danger} />
          <Text style={styles.rejectBtnText}>Reject</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.actionBtn, styles.approveBtn]}
          onPress={() => onApprove(draft.id)}
        >
          <Ionicons name="checkmark" size={16} color="#fff" />
          <Text style={styles.approveBtnText}>Approve</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

export default function ApprovalsScreen() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejectModal, setRejectModal] = useState<{ id: string } | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await getPendingActions();
      setDrafts(data.drafts);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load approvals');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleApprove = async (id: string) => {
    Alert.alert('Approve Draft', 'Send this action for execution?', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Approve',
        style: 'default',
        onPress: async () => {
          try {
            await approveAction(id);
            setDrafts((prev) => prev.filter((d) => d.id !== id));
          } catch (err) {
            Alert.alert('Error', err instanceof Error ? err.message : 'Approval failed');
          }
        },
      },
    ]);
  };

  const handleRejectConfirm = async () => {
    if (!rejectModal) return;
    try {
      await rejectAction(rejectModal.id, rejectReason || undefined);
      setDrafts((prev) => prev.filter((d) => d.id !== rejectModal.id));
    } catch (err) {
      Alert.alert('Error', err instanceof Error ? err.message : 'Rejection failed');
    } finally {
      setRejectModal(null);
      setRejectReason('');
    }
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

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Pending Approvals</Text>
        {drafts.length > 0 ? (
          <View style={styles.countBadge}>
            <Text style={styles.countBadgeText}>{drafts.length}</Text>
          </View>
        ) : null}
      </View>

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      <FlatList
        data={drafts}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <DraftCard
            draft={item}
            onApprove={handleApprove}
            onReject={(id) => setRejectModal({ id })}
          />
        )}
        contentContainerStyle={styles.list}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} tintColor={COLORS.primary} />}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="checkmark-circle-outline" size={48} color={COLORS.success} />
            <Text style={styles.emptyTitle}>All caught up!</Text>
            <Text style={styles.emptySubtitle}>No pending approvals.</Text>
          </View>
        }
      />

      {/* Reject Modal */}
      <Modal visible={!!rejectModal} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Reject Draft</Text>
            <Text style={styles.modalSubtitle}>Optionally provide a reason:</Text>
            <TextInput
              style={styles.modalInput}
              value={rejectReason}
              onChangeText={setRejectReason}
              placeholder="e.g. Wrong amount, not needed"
              placeholderTextColor={COLORS.textMuted}
              multiline
              numberOfLines={3}
              autoFocus
            />
            <View style={styles.modalActions}>
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalCancelBtn]}
                onPress={() => { setRejectModal(null); setRejectReason(''); }}
              >
                <Text style={styles.modalCancelText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity style={[styles.modalBtn, styles.modalRejectBtn]} onPress={handleRejectConfirm}>
                <Text style={styles.modalRejectText}>Reject</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 12,
    gap: 10,
  },
  title: { fontSize: 22, fontWeight: '800', color: COLORS.text },
  countBadge: {
    backgroundColor: COLORS.warning,
    borderRadius: 99,
    paddingHorizontal: 9,
    paddingVertical: 2,
  },
  countBadgeText: { color: '#fff', fontWeight: '700', fontSize: 13 },
  list: { paddingHorizontal: 16, paddingBottom: 24 },
  card: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 14,
    marginBottom: 10,
    ...SHADOW.sm,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  cardTypeTag: {
    backgroundColor: COLORS.primaryLight,
    borderRadius: 99,
    paddingHorizontal: 10,
    paddingVertical: 3,
  },
  cardTypeText: { fontSize: 12, fontWeight: '600', color: COLORS.primary },
  cardDate: { fontSize: 12, color: COLORS.textMuted },
  contentBox: {
    backgroundColor: COLORS.background,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  contentRow: { flexDirection: 'row', marginBottom: 4 },
  contentKey: { fontSize: 12, fontWeight: '600', color: COLORS.textSecondary, width: 90, flexShrink: 0 },
  contentValue: { fontSize: 12, color: COLORS.text, flex: 1 },
  expandBtn: { fontSize: 12, color: COLORS.primary, marginTop: 4, fontWeight: '500' },
  actionRow: { flexDirection: 'row', gap: 8 },
  actionBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: RADIUS.md,
    paddingVertical: 9,
    gap: 4,
  },
  rejectBtn: { borderWidth: 1, borderColor: COLORS.danger },
  rejectBtnText: { color: COLORS.danger, fontWeight: '600', fontSize: 14 },
  approveBtn: { backgroundColor: COLORS.primary },
  approveBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  errorBox: {
    backgroundColor: COLORS.dangerLight,
    marginHorizontal: 16,
    borderRadius: RADIUS.sm,
    padding: 10,
    marginBottom: 8,
  },
  errorText: { color: COLORS.danger, fontSize: 13 },
  emptyState: { alignItems: 'center', paddingTop: 80, gap: 8 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: COLORS.text },
  emptySubtitle: { fontSize: 14, color: COLORS.textSecondary },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
  },
  modalCard: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 20,
    width: '100%',
    ...SHADOW.md,
  },
  modalTitle: { fontSize: 17, fontWeight: '700', color: COLORS.text, marginBottom: 6 },
  modalSubtitle: { fontSize: 14, color: COLORS.textSecondary, marginBottom: 12 },
  modalInput: {
    borderWidth: 1,
    borderColor: COLORS.border,
    borderRadius: RADIUS.md,
    padding: 10,
    fontSize: 14,
    color: COLORS.text,
    textAlignVertical: 'top',
    minHeight: 70,
    marginBottom: 14,
  },
  modalActions: { flexDirection: 'row', gap: 8 },
  modalBtn: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: RADIUS.md,
    alignItems: 'center',
  },
  modalCancelBtn: { borderWidth: 1, borderColor: COLORS.border },
  modalCancelText: { color: COLORS.textSecondary, fontWeight: '600' },
  modalRejectBtn: { backgroundColor: COLORS.danger },
  modalRejectText: { color: '#fff', fontWeight: '700' },
});
