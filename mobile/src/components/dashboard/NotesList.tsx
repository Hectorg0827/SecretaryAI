import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { FollowUpNote } from '../../api/dashboard';
import { COLORS, RADIUS, SHADOW } from '../../theme';

interface Props {
  notes: FollowUpNote[];
  onToggle: (id: string, done: boolean) => void;
}

export default function NotesList({ notes, onToggle }: Props) {
  const active = notes.filter((n) => !n.done);
  const done = notes.filter((n) => n.done);
  const sorted = [...active, ...done];

  if (sorted.length === 0) {
    return (
      <View style={styles.empty}>
        <Ionicons name="checkmark-done-outline" size={28} color={COLORS.textMuted} />
        <Text style={styles.emptyText}>No follow-ups</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      {sorted.map((note, index) => (
        <View
          key={note.id}
          style={[styles.row, index < sorted.length - 1 && styles.rowBorder]}
        >
          <TouchableOpacity
            style={[styles.checkbox, note.done && styles.checkboxDone]}
            onPress={() => onToggle(note.id, !note.done)}
          >
            {note.done ? (
              <Ionicons name="checkmark" size={14} color="#fff" />
            ) : null}
          </TouchableOpacity>
          <View style={styles.content}>
            <Text style={[styles.noteText, note.done && styles.noteTextDone]}>
              {note.text}
            </Text>
            <View style={styles.meta}>
              {note.account_name ? (
                <Text style={styles.metaChip}>{note.account_name}</Text>
              ) : null}
              {note.due_date ? (
                <Text style={[
                  styles.dueDate,
                  new Date(note.due_date) < new Date() && !note.done && { color: COLORS.danger },
                ]}>
                  {new Date(note.due_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </Text>
              ) : null}
            </View>
          </View>
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
    alignItems: 'flex-start',
    padding: 12,
    gap: 10,
  },
  rowBorder: {
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: COLORS.border,
    justifyContent: 'center',
    alignItems: 'center',
    marginTop: 1,
    flexShrink: 0,
  },
  checkboxDone: {
    backgroundColor: COLORS.success,
    borderColor: COLORS.success,
  },
  content: { flex: 1 },
  noteText: { fontSize: 14, color: COLORS.text, lineHeight: 20 },
  noteTextDone: { color: COLORS.textMuted, textDecorationLine: 'line-through' },
  meta: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 4 },
  metaChip: {
    fontSize: 11,
    color: COLORS.primary,
    backgroundColor: COLORS.primaryLight,
    borderRadius: 99,
    paddingHorizontal: 7,
    paddingVertical: 1,
    fontWeight: '500',
  },
  dueDate: {
    fontSize: 11,
    color: COLORS.textMuted,
    fontWeight: '500',
  },
});
