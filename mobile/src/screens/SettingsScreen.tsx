import React, { useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  TextInput,
  Alert,
  ScrollView,
  Switch,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useAuthStore } from '../stores/authStore';
import { setApiBase, API_BASE } from '../api/client';
import { COLORS, RADIUS, SHADOW } from '../theme';

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  manager: 'Manager',
  sales_rep: 'Sales Rep',
  back_office: 'Back Office',
  viewer: 'Viewer',
};

function SettingRow({
  icon,
  label,
  value,
  onPress,
  danger,
  right,
}: {
  icon: string;
  label: string;
  value?: string;
  onPress?: () => void;
  danger?: boolean;
  right?: React.ReactNode;
}) {
  return (
    <TouchableOpacity
      style={styles.row}
      onPress={onPress}
      disabled={!onPress && !right}
      activeOpacity={onPress ? 0.7 : 1}
    >
      <View style={[styles.rowIcon, { backgroundColor: danger ? COLORS.dangerLight : COLORS.primaryLight }]}>
        <Ionicons name={icon as any} size={18} color={danger ? COLORS.danger : COLORS.primary} />
      </View>
      <View style={styles.rowContent}>
        <Text style={[styles.rowLabel, danger && { color: COLORS.danger }]}>{label}</Text>
        {value ? <Text style={styles.rowValue}>{value}</Text> : null}
      </View>
      {right ?? (onPress ? <Ionicons name="chevron-forward" size={16} color={COLORS.textMuted} /> : null)}
    </TouchableOpacity>
  );
}

export default function SettingsScreen() {
  const { user, logout } = useAuthStore();
  const [apiUrl, setApiUrl] = useState(API_BASE);
  const [editingUrl, setEditingUrl] = useState(false);
  const [notificationsEnabled, setNotificationsEnabled] = useState(true);

  const handleLogout = () => {
    Alert.alert('Sign out', 'Are you sure you want to sign out?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Sign out', style: 'destructive', onPress: () => logout() },
    ]);
  };

  const handleSaveApiUrl = () => {
    const cleaned = apiUrl.trim().replace(/\/$/, '');
    if (!cleaned.startsWith('http')) {
      Alert.alert('Invalid URL', 'API URL must start with http:// or https://');
      return;
    }
    setApiBase(cleaned);
    setApiUrl(cleaned);
    setEditingUrl(false);
    Alert.alert('Saved', 'API URL updated. Restart the app if needed.');
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.screenTitle}>Settings</Text>

        {/* Profile */}
        <Text style={styles.sectionLabel}>Profile</Text>
        <View style={styles.card}>
          <View style={styles.profileRow}>
            <View style={styles.avatarBox}>
              <Text style={styles.avatarLetter}>{(user?.name ?? 'U')[0].toUpperCase()}</Text>
            </View>
            <View style={styles.profileText}>
              <Text style={styles.profileName}>{user?.name ?? '—'}</Text>
              <Text style={styles.profileEmail}>{user?.email ?? '—'}</Text>
              <View style={styles.roleBadge}>
                <Text style={styles.roleBadgeText}>{ROLE_LABELS[user?.role ?? ''] ?? user?.role}</Text>
              </View>
            </View>
          </View>
        </View>

        {/* Preferences */}
        <Text style={styles.sectionLabel}>Preferences</Text>
        <View style={styles.card}>
          <SettingRow
            icon="notifications"
            label="Push notifications"
            right={
              <Switch
                value={notificationsEnabled}
                onValueChange={setNotificationsEnabled}
                trackColor={{ false: COLORS.border, true: COLORS.primary }}
                thumbColor="#fff"
              />
            }
          />
        </View>

        {/* Connection */}
        <Text style={styles.sectionLabel}>Connection</Text>
        <View style={styles.card}>
          {editingUrl ? (
            <View style={styles.urlEditSection}>
              <TextInput
                style={styles.urlInput}
                value={apiUrl}
                onChangeText={setApiUrl}
                autoCapitalize="none"
                autoCorrect={false}
                placeholder="https://api.yourserver.com"
                placeholderTextColor={COLORS.textMuted}
                keyboardType="url"
              />
              <View style={styles.urlBtnRow}>
                <TouchableOpacity
                  style={[styles.urlBtn, styles.urlCancelBtn]}
                  onPress={() => { setEditingUrl(false); setApiUrl(API_BASE); }}
                >
                  <Text style={styles.urlCancelText}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity style={[styles.urlBtn, styles.urlSaveBtn]} onPress={handleSaveApiUrl}>
                  <Text style={styles.urlSaveText}>Save</Text>
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <SettingRow
              icon="server"
              label="API Server"
              value={apiUrl}
              onPress={() => setEditingUrl(true)}
            />
          )}
        </View>

        {/* About */}
        <Text style={styles.sectionLabel}>About</Text>
        <View style={styles.card}>
          <SettingRow icon="information-circle" label="Version" value="1.0.0" />
          <View style={styles.divider} />
          <SettingRow icon="business" label="Company" value={user?.company_name ?? '—'} />
        </View>

        {/* Sign out */}
        <Text style={styles.sectionLabel}>Account</Text>
        <View style={styles.card}>
          <SettingRow icon="log-out" label="Sign out" onPress={handleLogout} danger />
        </View>

        <Text style={styles.footer}>Secretary AI · All data is private to your company</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.background },
  content: { paddingHorizontal: 16, paddingBottom: 40 },
  screenTitle: { fontSize: 22, fontWeight: '800', color: COLORS.text, paddingVertical: 16 },
  sectionLabel: { fontSize: 12, fontWeight: '700', color: COLORS.textMuted, textTransform: 'uppercase', letterSpacing: 0.5, marginTop: 16, marginBottom: 6 },
  card: { backgroundColor: COLORS.surface, borderRadius: RADIUS.lg, overflow: 'hidden', ...SHADOW.sm },
  profileRow: { flexDirection: 'row', alignItems: 'center', padding: 16, gap: 14 },
  avatarBox: { width: 52, height: 52, borderRadius: 26, backgroundColor: COLORS.primary, justifyContent: 'center', alignItems: 'center' },
  avatarLetter: { color: '#fff', fontWeight: '800', fontSize: 22 },
  profileText: { flex: 1 },
  profileName: { fontSize: 17, fontWeight: '700', color: COLORS.text },
  profileEmail: { fontSize: 13, color: COLORS.textSecondary, marginTop: 2 },
  roleBadge: { backgroundColor: COLORS.primaryLight, borderRadius: 99, paddingHorizontal: 8, paddingVertical: 2, alignSelf: 'flex-start', marginTop: 6 },
  roleBadgeText: { fontSize: 11, fontWeight: '700', color: COLORS.primary },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 13, gap: 12 },
  rowIcon: { width: 34, height: 34, borderRadius: RADIUS.sm, justifyContent: 'center', alignItems: 'center' },
  rowContent: { flex: 1 },
  rowLabel: { fontSize: 15, color: COLORS.text },
  rowValue: { fontSize: 12, color: COLORS.textMuted, marginTop: 1 },
  divider: { height: 1, backgroundColor: COLORS.border, marginHorizontal: 16 },
  urlEditSection: { padding: 16 },
  urlInput: { borderWidth: 1, borderColor: COLORS.border, borderRadius: RADIUS.md, padding: 10, fontSize: 14, color: COLORS.text, marginBottom: 10 },
  urlBtnRow: { flexDirection: 'row', gap: 8 },
  urlBtn: { flex: 1, borderRadius: RADIUS.md, paddingVertical: 9, alignItems: 'center' },
  urlCancelBtn: { borderWidth: 1, borderColor: COLORS.border },
  urlCancelText: { color: COLORS.textSecondary, fontWeight: '600' },
  urlSaveBtn: { backgroundColor: COLORS.primary },
  urlSaveText: { color: '#fff', fontWeight: '700' },
  footer: { marginTop: 24, textAlign: 'center', fontSize: 12, color: COLORS.textMuted },
});
