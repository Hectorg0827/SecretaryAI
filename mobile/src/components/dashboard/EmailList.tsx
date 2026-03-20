import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { Email } from '../../api/dashboard';
import { COLORS, RADIUS, SHADOW } from '../../theme';

interface Props {
  emails: Email[];
  onMarkRead: (id: string) => void;
}

function formatTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffH = diffMs / (1000 * 60 * 60);
  if (diffH < 1) return `${Math.round(diffMs / 60000)}m ago`;
  if (diffH < 24) return `${Math.round(diffH)}h ago`;
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function EmailList({ emails, onMarkRead }: Props) {
  if (emails.length === 0) {
    return (
      <View style={styles.empty}>
        <Ionicons name="mail-open-outline" size={28} color={COLORS.textMuted} />
        <Text style={styles.emptyText}>No unread emails</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {emails.map((email, index) => (
        <View
          key={email.id}
          style={[
            styles.row,
            index < emails.length - 1 && styles.rowBorder,
            !email.is_read && styles.unreadRow,
          ]}
        >
          <View style={[styles.avatarBox, email.is_read && styles.avatarBoxRead]}>
            <Text style={[styles.avatarLetter, email.is_read && styles.avatarLetterRead]}>
              {email.from_name[0]?.toUpperCase() ?? '?'}
            </Text>
          </View>
          <View style={styles.content}>
            <View style={styles.topRow}>
              <Text style={[styles.sender, !email.is_read && styles.senderUnread]} numberOfLines={1}>
                {email.from_name}
              </Text>
              <Text style={styles.time}>{formatTime(email.received_at)}</Text>
            </View>
            <Text style={[styles.subject, !email.is_read && styles.subjectUnread]} numberOfLines={1}>
              {email.subject}
            </Text>
            <Text style={styles.snippet} numberOfLines={1}>{email.snippet}</Text>
          </View>
          {!email.is_read ? (
            <TouchableOpacity style={styles.readBtn} onPress={() => onMarkRead(email.id)}>
              <View style={styles.unreadDot} />
            </TouchableOpacity>
          ) : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    marginBottom: 12,
    overflow: 'hidden',
    ...SHADOW.sm,
  },
  empty: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: 24,
    alignItems: 'center',
    gap: 8,
    marginBottom: 12,
    ...SHADOW.sm,
  },
  emptyText: { color: COLORS.textMuted, fontSize: 14 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 12,
    gap: 10,
  },
  rowBorder: {
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  unreadRow: {
    backgroundColor: COLORS.primaryLight,
  },
  avatarBox: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: COLORS.primary,
    justifyContent: 'center',
    alignItems: 'center',
    flexShrink: 0,
  },
  avatarBoxRead: {
    backgroundColor: COLORS.border,
  },
  avatarLetter: { color: '#fff', fontWeight: '700', fontSize: 15 },
  avatarLetterRead: { color: COLORS.textMuted },
  content: { flex: 1, minWidth: 0 },
  topRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  sender: { fontSize: 13, fontWeight: '500', color: COLORS.text, flex: 1 },
  senderUnread: { fontWeight: '700' },
  time: { fontSize: 11, color: COLORS.textMuted, flexShrink: 0, marginLeft: 4 },
  subject: { fontSize: 13, color: COLORS.textSecondary, marginTop: 1 },
  subjectUnread: { color: COLORS.text, fontWeight: '600' },
  snippet: { fontSize: 12, color: COLORS.textMuted, marginTop: 2 },
  readBtn: { padding: 4 },
  unreadDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: COLORS.primary,
  },
});
